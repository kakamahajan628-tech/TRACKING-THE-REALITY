import os, asyncio, aiosqlite, uvicorn, time, logging
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
# ASYNC PRODUCTION LEVEL SYSTEM LOGGING INTERFACES
# ========================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] TitaniumCoreV18: %(message)s',
    handlers=[
        logging.FileHandler("titanium_v18_execution.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("TitaniumOverlordV18")

# ========================================================
# 0. HARDENED CONFIGURATION & EXCURSION CONSTANTS
# ========================================================
class ProductionConfig:
    def __init__(self):
        self.tracked_symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        self.tg_app: Optional[Application] = None
        self.chat_id: Optional[str] = os.environ.get("TELEGRAM_CHAT_ID")
        self.token: Optional[str] = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.active_exchanges = []
        self.semaphore = asyncio.Semaphore(5)  # Global Concurrency Limit
        self.symbol_locks = {s: asyncio.Lock() for s in self.tracked_symbols} # Race Condition Firewall
        self.risk_per_trade_usd = 50.0
        self.max_notional_value_usd = 5000.0
        self.min_stop_distance_pct = 0.25 
        self.max_trade_age_seconds = 172800 # 48-Hour Hard Cut

config = ProductionConfig()

# ========================================================
# 1. PURE ASYNC DATABASE LAYER (Persistent Pooling + WAL)
# ========================================================
class SovereignAsyncDatabase:
    def __init__(self, db_name="sentinel_v18_titanium.db"):
        self.db_name = os.path.join(os.getcwd(), db_name)
        self.pool_conn: Optional[aiosqlite.Connection] = None  # Persistent Connection Pool

    async def init_db(self):
        """Asynchronously boots table constraints and performance optimizations."""
        self.pool_conn = await aiosqlite.connect(self.db_name)
        
        # Performance optimizations directly into the database engine
        await self.pool_conn.execute("PRAGMA journal_mode=WAL;")
        await self.pool_conn.execute("PRAGMA synchronous=NORMAL;")
        
        await self.pool_conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_lifecycle (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, symbol TEXT, direction TEXT,
                entry REAL, sl REAL, tp REAL, pos_size REAL,
                confidence REAL, session TEXT, status TEXT DEFAULT 'OPEN',
                f_choch INTEGER, f_sweep INTEGER, f_ob INTEGER, f_fvg INTEGER, f_bias INTEGER
            )
        """)
        await self.pool_conn.execute("CREATE TABLE IF NOT EXISTS cooldown_cache (symbol TEXT PRIMARY KEY, last_timestamp REAL)")
        await self.pool_conn.execute("CREATE TABLE IF NOT EXISTS execution_hashes (signal_hash TEXT PRIMARY KEY)")
        await self.pool_conn.commit()
        logger.info("⚡ Persistent DB Pool connection initialized with high-performance WAL pipelines.")

    async def check_active_cooldown(self, symbol: str, lock_interval=1800) -> bool:
        async with self.pool_conn.execute("SELECT last_timestamp FROM cooldown_cache WHERE symbol = ?", (symbol,)) as cursor:
            res = await cursor.fetchone()
            if res and (time.time() - res[0] < lock_interval): return False
            return True

    async def enforce_cooldown_lock(self, symbol: str):
        await self.pool_conn.execute("INSERT OR REPLACE INTO cooldown_cache VALUES (?, ?)", (symbol, time.time()))
        await self.pool_conn.commit()

    async def check_duplicate_open_trades(self, symbol: str) -> bool:
        async with self.pool_conn.execute("SELECT id FROM trade_lifecycle WHERE symbol = ? AND status = 'OPEN'", (symbol,)) as cursor:
            res = await cursor.fetchone()
            return res is not None

    async def verify_and_lock_signal_hash(self, signal_hash: str) -> bool:
        """Prevents duplicate message spam over Telegram nodes during concurrent iterations."""
        try:
            await self.pool_conn.execute("INSERT INTO execution_hashes (signal_hash) VALUES (?)", (signal_hash,))
            await self.pool_conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False 

    async def query_open_positions(self) -> List[Dict]:
        self.pool_conn.row_factory = aiosqlite.Row
        async with self.pool_conn.execute("SELECT * FROM trade_lifecycle WHERE status = 'OPEN'") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def update_lifecycle_outcome(self, trade_id: int, final_status: str):
        await self.pool_conn.execute("UPDATE trade_lifecycle SET status = ? WHERE id = ?", (final_status, trade_id))
        await self.pool_conn.commit()

    async def log_initial_intent(self, d: Dict, f_matrix: Dict):
        await self.pool_conn.execute("""
            INSERT INTO trade_lifecycle (timestamp, symbol, direction, entry, sl, tp, pos_size, confidence, session, f_choch, f_sweep, f_ob, f_fvg, f_bias)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (datetime.now(timezone.utc).isoformat(), d['symbol'], d['dir'], d['entry'], d['sl'], d['tp'], d['size'], d['conf'], d['session'],
              f_matrix["choch"], f_matrix["sweep"], f_matrix["ob"], f_matrix["fvg"], f_matrix["bias"]))
        await self.pool_conn.commit()

    async def query_performance_metrics(self) -> Dict:
        db_df = pd.read_sql_query("SELECT status FROM trade_lifecycle WHERE status IN ('WIN', 'LOSS', 'EXPIRED')", sqlite3.connect(self.db_name))
        if db_df.empty: return {"status_text": "📋 *Performance Matrix:* Insufficient log saturation."}
        
        total = len(db_df)
        wins = len(db_df[db_df['status'] == 'WIN'])
        losses = len(db_df[db_df['status'] == 'LOSS'])
        expired = len(db_df[db_df['status'] == 'EXPIRED'])
        wr = (wins / max(wins + losses, 1)) * 100
        profit_factor = (float(wins * 2.5) / max(losses, 1))
        
        return {"status_text": f"📋 *System Performance Analytics (Titan V18.2):*\nTotal Trades Processed: `{total}`\nActive Wins: `{wins}` | Losses: `{losses}`\nTemporal Expirations: `{expired}`\nWin-Rate (Closed): `{wr:.2f}%`\nProfit Factor Score: `{profit_factor:.2f}`"}

    async def query_bayesian_weights(self) -> Dict[str, float]:
        """Calculates adaptive weight matrices off historical logs."""
        base_weights = {"BIAS": 25.0, "SWEEP": 25.0, "CHOCH": 25.0, "OB": 15.0, "FVG": 10.0}
        try:
            df = pd.read_sql_query("SELECT * FROM trade_lifecycle WHERE status IN ('WIN', 'LOSS')", sqlite3.connect(self.db_name))
        except Exception: return base_weights
        
        if len(df) < 100: return base_weights 
        
        prior_win_rate, m_weight = 0.50, 10.0
        calculated_weights = {}
        features = {"f_choch": "CHOCH", "f_sweep": "SWEEP", "f_ob": "OB", "f_fvg": "FVG", "f_bias": "BIAS"}
        
        for db_col, weight_name in features.items():
            feature_trades = df[df[db_col] == 1]
            total_trades = len(feature_trades)
            if total_trades > 0:
                wins = len(feature_trades[feature_trades['status'] == 'WIN'])
                calculated_weights[weight_name] = ((wins + (prior_win_rate * m_weight)) / (total_trades + m_weight)) * 100
            else: calculated_weights[weight_name] = base_weights[weight_name]
                
        total_sum = sum(calculated_weights.values())
        for k in calculated_weights: calculated_weights[k] = (calculated_weights[k] / total_sum) * 100
        return calculated_weights

    async def close_pool(self):
        if self.pool_conn:
            await self.pool_conn.close()

db = SovereignAsyncDatabase()

# ========================================================
# TELEGRAM COMMUNICATION ENGINE PIPELINES
# ========================================================
async def send_telegram_alert(text: str):
    """Broadcasts algorithmic triggers and system reports directly to your personal chat ID."""
    if config.tg_app and config.chat_id:
        try: 
            await config.tg_app.bot.send_message(chat_id=config.chat_id, text=text, parse_mode="Markdown")
        except Exception as e: 
            logger.error(f"Telemetry broadcast failure: {str(e)}")

# ========================================================
# SESSION KILLZONE FILTER ENGINE (FIX: NameError Resolved)
# ========================================================
class SessionKillzoneFilter:
    @staticmethod
    def check_killzone() -> Tuple[bool, str]:
        current_hour_utc = datetime.now(timezone.utc).hour
        if 7 <= current_hour_utc <= 10: return True, "LONDON_OPEN"
        elif 12 <= current_hour_utc <= 15: return True, "NEW_YORK_OPEN"
        elif 0 <= current_hour_utc <= 3: return True, "ASIA_ACCUMULATION"
        return False, "OUTSIDE_KILLZONE"

# ========================================================
# 2. VECTORIZED STRUCTURE ANALYSIS ENGINE (NO LOOKAHEAD)
# ========================================================
class MarketStructureEngine:
    @staticmethod
    def calculate_true_atr(df: pd.DataFrame) -> pd.Series:
        h, l, c = df['h'].values, df['l'].values, df['c'].values
        tr = np.zeros(len(df))
        tr[0] = h[0] - l[0]
        for i in range(1, len(df)):
            tr[i] = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
        return pd.Series(tr).rolling(14).mean()

    @staticmethod
    def extract_geometry_sequence(df: pd.DataFrame, window=5) -> Tuple[pd.DataFrame, Dict]:
        """Pure Vectorized Backward-looking Swing tracking engine eliminates lookahead bias."""
        h, l, c, v = df['h'], df['l'], df['c'], df['v']
        
        rolling_max = h.shift(1).rolling(window=window).max()
        rolling_min = l.shift(1).rolling(window=window).min()
        
        last_confirmed_sh = rolling_max.iloc[-2]
        last_confirmed_sl = rolling_min.iloc[-2]
        
        fvg_valid = (l.shift(2) > h).iloc[-2]
        fvg_ce_rejected = False
        if fvg_valid:
            gap_top, gap_bottom = l.shift(2).iloc[-2], h.iloc[-2]
            ce_midpoint = (gap_top + gap_bottom) / 2
            if h.iloc[-2] >= ce_midpoint and c.iloc[-2] < ce_midpoint: fvg_ce_rejected = True
            
        true_liquidity_sweep = (h.iloc[-2] > last_confirmed_sh) and (c.iloc[-2] < last_confirmed_sh)
        true_bearish_bos = c.iloc[-2] < last_confirmed_sl
        
        vol_ma_20 = v.rolling(20).mean().iloc[-2] or 1.0
        displacement = (v.iloc[-2] > vol_ma_20 * 1.5) and (abs(c.iloc[-2] - df['o'].iloc[-2]) / max(h.iloc[-2] - l.iloc[-2], 1e-9) > 0.60)
        ob_active = (c.iloc[-3] > df['o'].iloc[-3]) and (c.iloc[-2] < l.iloc[-3]) and displacement

        return df, {"last_hl": last_confirmed_sl, "dealing_high": last_confirmed_sh, "dealing_low": last_confirmed_sl,
                    "sweep": true_liquidity_sweep, "bos": true_bearish_bos, "ob": ob_active, "fvg": fvg_valid, "ce_reject": fvg_ce_rejected}

# ========================================================
# 3. HIGH-AVAILABILITY FAILOVER & RETRY INTERFACES
# ========================================================
class HighAvailabilityNetworkProxy:
    @staticmethod
    async def fetch_ohlcv_with_backoff(exchange: ccxt.Exchange, symbol: str, timeframe: str, limit: int) -> Optional[List]:
        for attempt in range(3):
            try:
                return await asyncio.wait_for(exchange.fetch_ohlcv(symbol, timeframe, limit=limit), timeout=15)
            except (ccxt.NetworkError, ccxt.ExchangeError, asyncio.TimeoutError) as network_fault:
                backoff_delay = 2 ** attempt
                logger.warning(f"⚠️ Exchange Interface drop on [{exchange.id}] attempt ({attempt+1}/3). Retrying in {backoff_delay}s... Error: {str(network_fault)}")
                await asyncio.sleep(backoff_delay)
        return None

# ========================================================
# 4. CAPITAL PROTECTIONS & COST CACHE TUNING
# ========================================================
class InstitutionalRiskGovernor:
    @staticmethod
    def calculate_allocation_guarded(entry: float, sl: float) -> Tuple[float, float]:
        stop_distance = abs(entry - sl)
        if stop_distance == 0 or ((stop_distance / entry) * 100) < config.min_stop_distance_pct: return 0.0, 0.0
        raw_quantity = config.risk_per_trade_usd / stop_distance
        raw_notional = raw_quantity * entry
        if raw_notional > config.max_notional_value_usd:
            return config.max_notional_value_usd / entry, config.max_notional_value_usd
        return raw_quantity, raw_notional

class TradeLifecycleMonitor:
    @staticmethod
    async def audit_active_positions(price_cache: Dict[str, float]):
        open_trades = await db.query_open_positions()
        if not open_trades: return

        for position in open_trades:
            symbol = position['symbol']
            trade_id = position['id']
            
            trade_time = datetime.fromisoformat(position['timestamp']).replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - trade_time).total_seconds() > config.max_trade_age_seconds:
                await db.update_lifecycle_outcome(trade_id, "EXPIRED")
                await send_telegram_alert(f"⏳ *[LIFECYCLE EXPORT: EXPIRED]*\nAsset Node `{symbol}` auto-flushed at maturity ceiling.")
                continue

            live_price = price_cache.get(symbol)
            if not live_price: continue
                
            if position['direction'] == "SHORT":
                if live_price >= position['sl']:
                    await db.update_lifecycle_outcome(trade_id, "LOSS")
                    await send_telegram_alert(f"🛑 *[LIFECYCLE EXPORT: LOSS]*\nAsset `{symbol}` hit stop protection zone.")
                elif live_price <= position['tp']:
                    await db.update_lifecycle_outcome(trade_id, "WIN")
                    await send_telegram_alert(f"🎯 *[LIFECYCLE EXPORT: WIN]*\nAsset `{symbol}` reached absolute take-profit margins.")

# ========================================================
# 5. TITAN MASTER PROCESSING PIPELINE MANAGEMENT
# ========================================================
class CompleteSentinelEngine:
    async def process_market_execution(self, symbol: str, price_cache: Dict[str, float]):
        async with config.symbol_locks[symbol]: 
            if await db.check_duplicate_open_trades(symbol): return
            if not await db.check_active_cooldown(symbol): return
            if not SessionKillzoneFilter.check_killzone()[0]: return

            async with config.semaphore:
                l_data, h_data, i_data, selected_ex_id = None, None, None, None
                for ex in config.active_exchanges:
                    l_data = await HighAvailabilityNetworkProxy.fetch_ohlcv_with_backoff(ex, symbol, '15m', limit=100)
                    h_data = await HighAvailabilityNetworkProxy.fetch_ohlcv_with_backoff(ex, symbol, '4h', limit=300)
                    i_data = await HighAvailabilityNetworkProxy.fetch_ohlcv_with_backoff(ex, symbol, '1h', limit=100)
                    if l_data and h_data and i_data:
                        selected_ex_id = ex.id.upper()
                        break 
                
                if not l_data or not h_data or not i_data:
                    logger.error(f"❌ Ingestion Fault: Asset node {symbol} data mapping dropped across ALL failover profiles.")
                    return

                df_ltf = pd.DataFrame(l_data, columns=['t','o','h','l','c','v'])
                df_htf = pd.DataFrame(h_data, columns=['t','o','h','l','c','v'])
                df_itf = pd.DataFrame(i_data, columns=['t','o','h','l','c','v'])

                atr_series = MarketStructureEngine.calculate_true_atr(df_ltf)
                atr_now = atr_series.iloc[-2] or 1e-9
                atr_ma_20 = atr_series.rolling(20).mean().iloc[-2] or 1e-9

                live_market_rate = price_cache.get(symbol) or df_ltf['c'].iloc[-2]

                ema20_4h = df_htf['c'].ewm(span=20).mean().iloc[-2]
                ema50_4h = df_htf['c'].ewm(span=50).mean().iloc[-2]
                ema200_4h = df_htf['c'].ewm(span=200).mean().iloc[-2]
                
                ema_spread_pct = (abs(ema20_4h - ema50_4h) / ema50_4h) * 100
                if not (ema20_4h < ema50_4h < ema200_4h and df_itf['c'].iloc[-2] < df_itf['c'].ewm(span=20).mean().iloc[-2] and ema_spread_pct >= 0.3):
                    return

                df_ltf, smc = MarketStructureEngine.extract_geometry_sequence(df_ltf)
                in_premium = live_market_rate > ((smc["dealing_high"] + smc["dealing_low"]) / 2)

                if not (smc["sweep"] and smc["bos"] and (smc["ob"] or smc["fvg"]) and in_premium and (atr_now > atr_ma_20)):
                    return 

                calibrated_weights = await db.query_bayesian_weights()
                confidence_index = (calibrated_weights["BIAS"] + calibrated_weights["SWEEP"] + calibrated_weights["CHOCH"]) 

                execution_date_token = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H')
                signal_hash_token = f"{symbol}_SHORT_{execution_date_token}"
                if not await db.verify_and_lock_signal_hash(signal_hash_token): return

                sl_target = max(smc["dealing_high"], live_market_rate + (atr_now * 1.5))
                tp_target = live_market_rate - (abs(sl_target - live_market_rate) * 2.5)
                
                units_size, true_notional = InstitutionalRiskGovernor.calculate_allocation_guarded(live_market_rate, sl_target)
                if units_size <= 0: return

                intent_packet = {'symbol': symbol, 'dir': 'SHORT', 'entry': live_market_rate, 'sl': sl_target, 'tp': tp_target, 'size': units_size, 'conf': confidence_index, 'session': "ACTIVE"}
                f_matrix = {"bias": 1, "sweep": 1, "choch": 1, "ob": 1 if smc["ob"] else 0, "fvg": 1 if smc["fvg"] else 0}
                
                await db.log_initial_intent(intent_packet, f_matrix)
                await db.enforce_cooldown_lock(symbol)
                
                await send_telegram_alert(
                    f"🦅 *SOVEREIGN TITANIUM VECTOR INITIATED*\n"
                    f"========================================\n"
                    f"• *Asset Node:* `{symbol}` | *Ingestion Source:* `{selected_ex_id}`\n"
                    f"• *Bayesian Conf Resolve:* `{confidence_index:.2f}%` | *Consensus: 100%*\n"
                    f"----------------------------------------\n"
                    f"➔ *Execution Entry:* `{live_market_rate}`\n"
                    f"➔ *SL Protection Boundary:* `{sl_target:.2f}` | *TP Target:* `{tp_target:.2f}`\n"
                    f"➔ *Calculated Sizing Units:* `{units_size:.4f}` | *Notional value:* `${true_notional:.2f}`\n"
                    f"========================================\n"
                )

# ========================================================
# 6. LIFESPAN SYSTEM ROUTING INTERFACES
# ========================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    config.active_exchanges = [ccxt.okx({"enableRateLimit": True}), ccxt.gate({"enableRateLimit": True})]
    
    if config.token and config.chat_id:
        tg = Application.builder().token(config.token).build()
        tg.add_handler(CommandHandler("matrix", manual_matrix_audit))
        tg.add_handler(CommandHandler("stats", query_performance_telemetry))
        
        await tg.initialize()
        
        # Webhook parameters flush on pre-flight boot phase to drop duplication loop locks cleanly
        await tg.bot.delete_webhook(drop_pending_updates=True)
        logger.info("🗑️ Pre-flight validation: Expired Telegram webhook sessions flushed cleanly.")
        
        await tg.start()
        await tg.updater.start_polling(drop_pending_updates=True)
        config.tg_app = tg
        logger.info("Sovereign Alpha Matrix Communications Layer established.")
        
    loop_task = asyncio.create_task(core_processing_loop())
    yield  
    
    loop_task.cancel()
    try: await loop_task
    except asyncio.CancelledError: logger.info("Core background processing cancel confirmed.")
        
    if config.tg_app:
        await config.tg_app.updater.stop()
        await config.tg_app.stop()
        await config.tg_app.shutdown() 
        
    for ex in config.active_exchanges: await ex.close()
    await db.close_pool() 
    logger.info("🛑 Sovereign core infrastructure completely unmounted. Secure exit protocol terminated.")

async def core_processing_loop():
    engine = CompleteSentinelEngine()
    while True:
        try:
            if not config.active_exchanges:
                await asyncio.sleep(5)
                continue
                
            primary_exchange = config.active_exchanges[0]
            global_price_cache = {}
            for token in config.tracked_symbols:
                try:
                    ticker_data = await asyncio.wait_for(primary_exchange.fetch_ticker(token), timeout=10)
                    global_price_cache[token] = float(ticker_data['last'])
                except Exception: pass
            
            await asyncio.gather(*[engine.process_market_execution(s, global_price_cache) for s in list(config.tracked_symbols)])
            await TradeLifecycleMonitor.audit_active_positions(global_price_cache)
        except Exception as err:
            logger.error(f"Sovereign scheduler cycle deviation exception handled: {str(err)}")
        await asyncio.sleep(300)

async def manual_matrix_audit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != config.chat_id: return
    await update.message.reply_text("🔬 *Executing Sovereign Alpha Scan across dual exchange matrices...*")
    temp_cache = {}
    try:
        t_data = await config.active_exchanges[0].fetch_tickers(list(config.tracked_symbols))
        for k, v in t_data.items(): temp_cache[k] = float(v['last'])
    except Exception: pass
    engine = CompleteSentinelEngine()
    await asyncio.gather(*[engine.process_market_execution(s, temp_cache) for s in config.tracked_symbols])

async def query_performance_telemetry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != config.chat_id: return
    metrics = await db.query_performance_metrics()
    await update.message.reply_text(metrics["status_text"], parse_mode="Markdown")

app = FastAPI(lifespan=lifespan)
@app.api_route("/", methods=["GET", "HEAD"])
def live_health_proxy(): return {"status": "ONLINE", "framework": "Sovereign Framework Core V18.2 Hardened"}

if __name__ == "__main__": uvicorn.run("bot:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), workers=1)
