import os, asyncio, sqlite3, uvicorn, time
import pandas as pd
import numpy as np
import ccxt.async_support as ccxt
from fastapi import FastAPI
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ========================================================
# 0. GLOBAL CONFIG & RATE LIMITING
# ========================================================
class GlobalConfig:
    def __init__(self):
        self.tracked_symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        self.tg_app: Optional[Application] = None
        self.chat_id: Optional[str] = os.environ.get("TELEGRAM_CHAT_ID")
        self.token: Optional[str] = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.active_exchanges = []
        self.semaphore = asyncio.Semaphore(5) # BUG #8 FIX: Rate limit protection

config = GlobalConfig()

# ========================================================
# 1. PERSISTENT DATABASE (BUG #6 & #10 FIX)
# ========================================================
class InstitutionalDatabase:
    def __init__(self, db_name="sentinel_v10.db"):
        self.db_name = os.path.join(os.getcwd(), db_name)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_name) as conn:
            # Combined Signals and Cooldowns table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME, symbol TEXT, direction TEXT,
                    entry REAL, sl REAL, tp REAL, pos_size REAL,
                    confidence REAL, exchange TEXT, htf_bias TEXT
                )
            """)
            conn.execute("CREATE TABLE IF NOT EXISTS cooldowns (symbol TEXT PRIMARY KEY, last_time REAL)")

    def check_cooldown(self, symbol: str, interval=1800) -> bool:
        with sqlite3.connect(self.db_name) as conn:
            res = conn.execute("SELECT last_time FROM cooldowns WHERE symbol = ?", (symbol,)).fetchone()
            if res and (time.time() - res[0] < interval): return False
            return True

    def update_cooldown(self, symbol: str):
        with sqlite3.connect(self.db_name) as conn:
            conn.execute("INSERT OR REPLACE INTO cooldowns (symbol, last_time) VALUES (?, ?)", (symbol, time.time()))

    def log_signal(self, d: Dict):
        with sqlite3.connect(self.db_name) as conn:
            conn.execute("""
                INSERT INTO signals (timestamp, symbol, direction, entry, sl, tp, pos_size, confidence, exchange, htf_bias)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (datetime.now(timezone.utc), d['symbol'], d['dir'], d['entry'], d['sl'], d['tp'], d['size'], d['conf'], d['ex'], d['htf']))

# Startup global DB instance
db = InstitutionalDatabase()

# ========================================================
# 2. ADVANCED QUANT LOGIC (BUG #1, #2, #3, #5, #7 FIX)
# ========================================================
class TradingEngine:
    @staticmethod
    def get_market_context(df_htf: pd.DataFrame, df_itf: pd.DataFrame) -> str:
        """4H Bias + 1H Structure (Top-Down Filter)."""
        # BUG #1 FIX: Using 'c' instead of 'close'
        ema20 = df_htf['c'].ewm(span=20).mean().iloc[-1]
        ema50 = df_htf['c'].ewm(span=50).mean().iloc[-1]
        
        # 1H structure check (Price below 1H EMA)
        itf_ema = df_itf['c'].ewm(span=20).mean().iloc[-1]
        itf_price = df_itf['c'].iloc[-1]
        
        if ema20 < ema50 and itf_price < itf_ema: return "STRONG_BEARISH"
        if ema20 < ema50: return "BEARISH"
        return "BULLISH"

    @staticmethod
    def calculate_score(df: pd.DataFrame) -> Tuple[float, float]:
        """Continuous scoring with guard for division by zero."""
        # BUG #2 FIX: Correct column mapping
        o, h, l, c, v = df['o'].values, df['h'].values, df['l'].values, df['c'].values, df['v'].values
        # BUG #3 FIX: ATR column mapping
        atr = (df['h'] - df['l']).rolling(14).mean().iloc[-1] or 1e-9
        
        # Continuous Scoring Logic
        upper_wick = h[-1] - max(c[-1], o[-1])
        sweep_score = min((upper_wick / (atr * 0.6)) * 30, 30)
        
        candle_range = max(h[-1] - l[-1], 1e-9) # BUG #11 GUARD
        displacement = (abs(c[-1] - o[-1]) / candle_range) * 40
        
        vol_ma = df['v'].rolling(20).mean().iloc[-1] or 1.0
        vol_score = min((v[-1] / vol_ma) * 30, 30)
        
        total_score = sweep_score + displacement + vol_score
        return total_score, atr

# ========================================================
# 3. POSITION SIZING & RISK (ISSUE #7 FIX)
# ========================================================
def calculate_position_size(entry, sl, risk_usd=100):
    """Calculates quantity based on 1% risk of account or fixed USD risk."""
    stop_dist = abs(entry - sl)
    if stop_dist == 0: return 0
    return risk_usd / stop_dist

# ========================================================
# 4. CORE SCANNER PIPELINE
# ========================================================
async def scan_symbol(symbol: str):
    async with config.semaphore: # Rate limiting
        if not db.check_cooldown(symbol): return

        df_ltf, df_itf, df_htf, active_ex = None, None, None, None
        for ex in config.active_exchanges:
            try:
                # Fetching 3 Timeframes
                l_data = await ex.fetch_ohlcv(symbol, '15m', limit=100)
                i_data = await ex.fetch_ohlcv(symbol, '1h', limit=50)
                h_data = await ex.fetch_ohlcv(symbol, '4h', limit=50)
                
                df_ltf = pd.DataFrame(l_data, columns=['t','o','h','l','c','v'])
                df_itf = pd.DataFrame(i_data, columns=['t','o','h','l','c','v'])
                df_htf = pd.DataFrame(h_data, columns=['t','o','h','l','c','v'])
                active_ex = ex.id.upper()
                break
            except: continue

        if df_ltf is None: return

        engine = TradingEngine()
        context = engine.get_market_context(df_htf, df_itf)
        score, atr = engine.calculate_score(df_ltf)

        # ISSUE #5 FIX: Lowered threshold to 58% for realistic signal frequency
        if "BEARISH" in context and score > 58:
            entry = df_ltf['c'].iloc[-1]
            sl = df_ltf['h'].iloc[-5:].max() + (atr * 0.2)
            tp = entry - (abs(entry - sl) * 2.5) # Reduced RR to 2.5 for higher hit rate
            pos_size = calculate_position_size(entry, sl, risk_usd=50) # Assuming $50 risk per trade
            
            signal = {
                'symbol': symbol, 'dir': 'SHORT', 'entry': entry, 'sl': sl, 'tp': tp,
                'size': pos_size, 'conf': score, 'ex': active_ex, 'htf': context
            }
            
            db.log_signal(signal)
            db.update_cooldown(symbol)
            
            msg = (
                f"🦅 *V10 PRO SIGNAL: {symbol}*\n"
                f"Exchange: `{active_ex}` | Score: `{score:.1f}`\n"
                f"Bias: `{context}`\n"
                f"----------------------------------\n"
                f"Entry: `{entry}`\n"
                f"SL: `{sl:.2f}` | TP: `{tp:.2f}`\n"
                f"Pos Size: `{pos_size:.3f}` units\n"
                f"Risk: `$50.00`"
            )
            if config.tg_app:
                await config.tg_app.bot.send_message(chat_id=config.chat_id, text=msg, parse_mode="Markdown")

# ========================================================
# 5. STARTUP & LIFESPAN (BUG #4 FIX)
# ========================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # BUG #4 FIX: Telegram Guard
    if config.token and config.chat_id:
        tg = Application.builder().token(config.token).build()
        tg.add_handler(CommandHandler("matrix", lambda u, c: asyncio.create_task(manual_matrix(u, c))))
        await tg.initialize()
        await tg.start()
        await tg.updater.start_polling(drop_pending_updates=True)
        config.tg_app = tg
    
    config.active_exchanges = [ccxt.okx(), ccxt.gate()]
    
    async def loop():
        while True:
            await asyncio.gather(*[scan_symbol(s) for s in config.tracked_symbols])
            await asyncio.sleep(300) # Hard 5-min loop
    
    task = asyncio.create_task(loop())
    yield
    task.cancel()
    if config.tg_app: await config.tg_app.stop()

async def manual_matrix(update, context):
    await update.message.reply_text("🔬 *V10 Pro Audit in progress...*")
    await asyncio.gather(*[scan_symbol(s) for s in config.tracked_symbols])

app = FastAPI(lifespan=lifespan)

@app.get("/")
def root(): return {"status": "V10 Institutional Active", "symbols": config.tracked_symbols}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
