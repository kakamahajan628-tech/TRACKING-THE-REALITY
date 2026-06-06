import os
import pandas as pd
import numpy as np
import ccxt.async_support as ccxt
import asyncio
import sqlite3
import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

# Telegram App Libraries
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ========================================================
# 0. SYSTEM STATE LAYER (Dynamic Persistent Variables)
# ========================================================
class DynamicGlobalState:
    def __init__(self):
        self.tracked_symbols = ["BTC/USDT", "ETH/USDT"]  # Adaptive Matrix Queue
        self.tg_app: Optional[Application] = None
        self.chat_id: Optional[str] = os.environ.get("TELEGRAM_CHAT_ID")
        self.token: Optional[str] = os.environ.get("TELEGRAM_BOT_TOKEN")

global_state = DynamicGlobalState()

# ========================================================
# 1. CORE COMMUNICATIONS LOGISTICS (Telegram Engine)
# ========================================================
async def send_telegram_alert(text: str):
    """Broadcasts algorithmic triggers and system reports directly to your personal chat ID."""
    if global_state.tg_app and global_state.chat_id:
        try:
            await global_state.tg_app.bot.send_message(chat_id=global_state.chat_id, text=text, parse_mode="Markdown")
        except Exception as e:
            print(f"⚠️ Telemetry broadcast failure: {str(e)}")

async def tg_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != global_state.chat_id: return
    msg = (
        "🦅 *QUANTUM-SENTINEL V8 DUAL-EXCHANGE ACTIVE*\n"
        "========================================\n"
        "Framework Command Access Layer:\n\n"
        "🔹 `/add COIN` - Queue structural tracking node (e.g., `/add SOL/USDT`)\n"
        "🔹 `/remove COIN` - Drop vector coordinate (e.g., `/remove ETH/USDT`)\n"
        "🔹 `/list` - Output contemporary inventory registry\n"
        "🔹 `/matrix` - Execute hybrid OKX & Gate.io SMC snapshot"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def tg_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != global_state.chat_id: return
    if not context.args:
        await update.message.reply_text("⚠️ Syntax error. Use: `/add SOL/USDT`")
        return
    coin = context.args[0].upper()
    if coin not in global_state.tracked_symbols:
        global_state.tracked_symbols.append(coin)
        await update.message.reply_text(f"✅ Structural tracker active for coordinate node: *{coin}*", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ Vector token *{coin}* is already linked.")

async def tg_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != global_state.chat_id: return
    if not context.args:
        await update.message.reply_text("⚠️ Syntax error. Use: `/remove ETH/USDT`")
        return
    coin = context.args[0].upper()
    if coin in global_state.tracked_symbols:
        global_state.tracked_symbols.remove(coin)
        await update.message.reply_text(f"❌ Dropped target node *{coin}* from active processing stack.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ Vector node *{coin}* could not be located.")

async def tg_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != global_state.chat_id: return
    if not global_state.tracked_symbols:
        await update.message.reply_text("📋 Operational registry queue is blank.")
        return
    active_tokens = "\n".join([f"   ↳ Code Node: `{token}`" for token in global_state.tracked_symbols])
    await update.message.reply_text(f"📋 *Active Operational Processing Registry:*\n\n{active_tokens}", parse_mode="Markdown")

async def tg_matrix_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates a cross-exchange evaluation report scanning both OKX and Gate.io data maps."""
    if str(update.effective_chat.id) != global_state.chat_id: return
    if not global_state.tracked_symbols:
        await update.message.reply_text("📋 Processing context aborted. Active execution matrix queue is empty.")
        return

    await update.message.reply_text("🔍 *Deep-scanning OKX & Gate.io Orderbooks... Processing Multi-Exchange Alpha Matrix.*", parse_mode="Markdown")
    
    # Dual Network Ingestion Nodes Initialization
    okx_ex = ccxt.okx({"enableRateLimit": True})
    gate_ex = ccxt.gate({"enableRateLimit": True})
    engine = CompleteSentinelEngine()
    
    try:
        for symbol in global_state.tracked_symbols:
            # Parallel data streams orchestration block
            okx_ohlcv, gate_ohlcv = None, None
            try:
                okx_ohlcv = await okx_ex.fetch_ohlcv(symbol, '15m', limit=100)
            except Exception: pass
            try:
                gate_ohlcv = await gate_ex.fetch_ohlcv(symbol, '15m', limit=100)
            except Exception: pass

            # Select premium stream based on absolute density profiles
            selected_ohlcv = okx_ohlcv if okx_ohlcv else gate_ohlcv
            source_node = "OKX ENGINE" if okx_ohlcv else ("GATE.IO LIQUIDITY" if gate_ohlcv else "FAILED_NODE")
            
            if not selected_ohlcv:
                await update.message.reply_text(f"⚠️ *Asset Coordinate* `{symbol}` *inaccessible on both OKX & Gate.io layers.*", parse_mode="Markdown")
                continue
                
            df = pd.DataFrame(selected_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df, state_meta = engine.structure_engine.map_market_geometry(df)
            
            closes, volumes = df['close'].values, df['volume'].values
            vol_ma = df['volume'].rolling(20).mean().values
            vol_valid = volumes[-1] > vol_ma[-1] * 1.5 if not np.isnan(vol_ma[-1]) else False
            
            true_bear_choch = True if (state_meta["last_hl"] and closes[-1] < state_meta["last_hl"] and vol_valid) else False
            liquidity = TrueLifecycleScanner.calculate_clustered_liquidity(df)
            
            true_bear_sweep = False
            if liquidity["EQH"]:
                max_eqh_target = max(liquidity["EQH"])
                if df['high'].iloc[-1] > max_eqh_target and closes[-1] < max_eqh_target: true_bear_sweep = True
                    
            fvg = TrueLifecycleScanner.scan_fvg_depth_profiles(df)
            ob = TrueLifecycleScanner.qualify_institutional_blocks(df, state_meta)
            equilibrium = (state_meta["dealing_high"] + state_meta["dealing_low"]) / 2
            in_premium = closes[-1] > equilibrium
            
            calibrated_weights = engine.db.query_bayesian_weights()
            score = 0
            if in_premium: score += calibrated_weights["BIAS"]
            if true_bear_sweep: score += calibrated_weights["SWEEP"]
            if (true_bear_choch or state_meta["bos_confirmed"]): score += calibrated_weights["CHOCH"]
            if (ob["ob_active"] or ob["breaker_active"]): score += calibrated_weights["OB"]
            if (fvg["fvg_valid"] or fvg["ce_rejected"]): score += calibrated_weights["FVG"]
            
            bias_status = "🔴 Premium Invalidation Zone" if in_premium else "🟢 Discount Accumulation Zone"
            sweep_status = "🔥 POSITIVE (Stop Hunt Verified)" if true_bear_sweep else "❌ Negative (No Clustered Hunt)"
            choch_status = "⚡ CHOCH Shift Confirmed" if true_bear_choch else ("📉 BOS Structural Continuation" if state_meta["bos_confirmed"] else "⏳ Consolidation Equilibrium Balance")
            ob_status = "🏰 Active Order Block Mitigation" if ob["ob_active"] else ("⚡ Breaker Block Conversion" if ob["breaker_active"] else "❌ Inactive Institutional Presence")
            fvg_status = "🎯 Imbalance Active (Unfilled Pocket)" if fvg["fvg_valid"] else ("⚠️ 50% CE Rejection Vector" if fvg["ce_rejected"] else "✅ Balanced Price Delivery")

            report_layout = (
                f"🔬 *SENTINEL QUANTUM V8 DUAL AUDIT: {symbol}*\n"
                f"========================================\n"
                f"🛰️ *Liquidity Core Ingestion Source:* `{source_node}`\n"
                f"🧠 *Bayesian Confidence Matrix Index:* `{score:.2f}%` / 100%\n"
                f"----------------------------------------\n"
                f"📈 *Structural State Engine:* {choch_status}\n"
                f"🎯 *Liquidity Cluster Pool:* {sweep_status}\n"
                f"🏰 *Institutional Order Block:* {ob_status}\n"
                f"🌊 *FVG Imbalance Lifecycle:* {fvg_status}\n"
                f"🎚️ *Dealing Range Evaluation:* {bias_status}\n"
                f"========================================\n"
            )
            await update.message.reply_text(report_layout, parse_mode="Markdown")

        await okx_ex.close()
        await gate_ex.close()
    except Exception as master_err:
        await okx_ex.close()
        await gate_ex.close()
        await update.message.reply_text(f"❌ *Matrix Infrastructure Audit Exception:* `{str(master_err)}`", parse_mode="Markdown")

# ========================================================
# 2. MASTER APPLICATION SHEDULER LIFESPAN CONTAINER
# ========================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n" + "="*60)
    print(" 🔥 STARTING THE SENTINEL QUANTUM CORE ENGINE CLOUD GATEWAY 🔥")
    print("="*60)
    
    if global_state.token:
        tg_builder = Application.builder().token(global_state.token).build()
        tg_builder.add_handler(CommandHandler("start", tg_start_command))
        tg_builder.add_handler(CommandHandler("add", tg_add_command))
        tg_builder.add_handler(CommandHandler("remove", tg_remove_command))
        tg_builder.add_handler(CommandHandler("list", tg_list_command))
        tg_builder.add_handler(CommandHandler("matrix", tg_matrix_command))
        
        await tg_builder.initialize()
        await tg_builder.start()
        await tg_builder.updater.start_polling(drop_pending_updates=True)
        global_state.tg_app = tg_builder
        print("📊 Institutional Telegram System interface bound and active.")
    else:
        print("⚠️ Initialization warning: System requires TELEGRAM_BOT_TOKEN.")

    engine = CompleteSentinelEngine()
    quant_task = asyncio.create_task(engine.engine_core_loop())
    
    yield  
    
    quant_task.cancel()
    if global_state.tg_app:
        await global_state.tg_app.updater.stop()
        await global_state.tg_app.stop()
        await global_state.tg_app.shutdown()
    print("🛑 System components unmounted successfully.")

app = FastAPI(lifespan=lifespan)

@app.api_route("/", methods=["GET", "HEAD"])
def health_check():
    return {"status": "HEALTHY", "engine": "The Sentinel Quantum Framework V8 Dual Core", "active_tracking_nodes": len(global_state.tracked_symbols)}

# ========================================================
# 3. DATABASE ALPHA REGISTRY LAYER (SQLite Map)
# ========================================================
class AdvancedSignalDatabase:
    def __init__(self, db_name="sentinel_quantum_v8.db"):
        self.db_name = os.path.join(os.getcwd(), db_name)
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_name) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS quantitative_journal (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT, symbol TEXT, direction TEXT,
                        entry_price REAL, stop_loss REAL, take_profit REAL,
                        final_pnl REAL, trade_status TEXT,
                        feature_choch INTEGER, feature_sweep INTEGER,
                        feature_ob INTEGER, feature_fvg INTEGER, feature_premium INTEGER,
                        calculated_confidence REAL
                    )
                """)
                conn.commit()
        except Exception as db_err:
            print(f"⚠️ SQL Persistence warning block bypassed: {str(db_err)}")

    def log_trade_intent(self, symbol: str, direction: str, entry: float, sl: float, tp: float, conf: float, f_matrix: Dict):
        try:
            with sqlite3.connect(self.db_name) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO quantitative_journal (
                        timestamp, symbol, direction, entry_price, stop_loss, take_profit,
                        final_pnl, trade_status, feature_choch, feature_sweep, feature_ob,
                        feature_fvg, feature_premium, calculated_confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, 0.0, 'OPEN', ?, ?, ?, ?, ?, ?)
                """, (datetime.now(timezone.utc).isoformat(), symbol, direction, entry, sl, tp,
                      f_matrix["choch"], f_matrix["sweep"], f_matrix["ob"], f_matrix["fvg"], f_matrix["bias"], conf))
                conn.commit()
        except Exception as log_err:
            print(f"❌ Data storage drop error: {str(log_err)}")

    def query_bayesian_weights(self) -> Dict[str, float]:
        base_weights = {"BIAS": 20.0, "SWEEP": 20.0, "CHOCH": 25.0, "OB": 15.0, "FVG": 20.0}
        try:
            with sqlite3.connect(self.db_name) as conn:
                df = pd.read_sql_query("SELECT * FROM quantitative_journal WHERE trade_status = 'CLOSED'", conn)
            if df.empty: return base_weights
            prior_win_rate, m_weight = 0.50, 10.0
            calculated_weights = {}
            features = {"feature_choch": "CHOCH", "feature_sweep": "SWEEP", "feature_ob": "OB", "feature_fvg": "FVG", "feature_premium": "BIAS"}
            for db_col, weight_name in features.items():
                feature_trades = df[df[db_col] == 1]
                total_trades = len(feature_trades)
                if total_trades > 0:
                    wins = len(feature_trades[feature_trades['final_pnl'] > 0])
                    bayesian_wr = (wins + (prior_win_rate * m_weight)) / (total_trades + m_weight)
                    calculated_weights[weight_name] = float(bayesian_wr * 100)
                else:
                    calculated_weights[weight_name] = base_weights[weight_name]
            total_sum = sum(calculated_weights.values())
            for k in calculated_weights: calculated_weights[k] = (calculated_weights[k] / total_sum) * 100
            return calculated_weights
        except Exception: return base_weights

# ========================================================
# 4. QUANT ALGORITHMIC PROCESSING LAYERS
# ========================================================
class StructuralStateEngine:
    def __init__(self, sensitivity: int = 3):
        self.sensitivity = sensitivity

    def map_market_geometry(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict]:
        if len(df) < 20: return df, {"last_hl": None, "last_lh": None, "dealing_high": None, "dealing_low": None, "bos_confirmed": False}
        highs, lows, closes = df['high'].values, df['low'].values, df['close'].values
        df['swing_high'], df['swing_low'] = 0.0, 0.0
        sh_points, sl_points = [], []
        
        for i in range(self.sensitivity, len(df) - self.sensitivity):
            if all(highs[i] >= highs[i-j] for j in range(1, self.sensitivity+1)) and all(highs[i] > highs[i+j] for j in range(1, self.sensitivity+1)):
                df.at[df.index[i], 'swing_high'] = highs[i]
                sh_points.append(highs[i])
            if all(lows[i] <= lows[i-j] for j in range(1, self.sensitivity+1)) and all(lows[i] < lows[i+j] for j in range(1, self.sensitivity+1)):
                df.at[df.index[i], 'swing_low'] = lows[i]
                sl_points.append(lows[i])

        last_valid_hl = sl_points[-2] if len(sl_points) >= 2 else (sl_points[-1] if sl_points else None)
        last_valid_lh = sh_points[-2] if len(sh_points) >= 2 else (sh_points[-1] if sh_points else None)
        dealing_high = sh_points[-1] if sh_points else highs.max()
        dealing_low = sl_points[-1] if sl_points else lows.min()
        
        true_bearish_bos = False
        if len(sl_points) >= 1:
            recent_low_target = sl_points[-1]
            atr_approx = np.mean(highs[-14:] - lows[-14:])
            body_size = abs(closes[-1] - df['open'].iloc[-1])
            if closes[-1] < recent_low_target and body_size > (atr_approx * 0.75): true_bearish_bos = True

        return df, {"last_hl": last_valid_hl, "last_lh": last_valid_lh, "dealing_high": dealing_high, "dealing_low": dealing_low, "bos_confirmed": true_bearish_bos}

class TrueLifecycleScanner:
    @staticmethod
    def calculate_clustered_liquidity(df: pd.DataFrame, threshold=0.0005) -> Dict:
        highs = df[df['swing_high'] > 0]['swing_high'].values
        eqh_clusters = []
        for h in highs:
            if any(abs(h - x) / h < threshold and x != h for x in highs):
                level = float(np.mean([x for x in highs if abs(x - h) / h < threshold]))
                if level not in eqh_clusters: eqh_clusters.append(level)
        return {"EQH": eqh_clusters}

    @staticmethod
    def scan_fvg_depth_profiles(df: pd.DataFrame) -> Dict:
        l, h, c = df['low'].values, df['high'].values, df['close'].values
        open_bear_fvgs = []
        for i in range(2, len(df)):
            if l[i-2] > h[i]:
                gap_top, gap_bottom = l[i-2], h[i]
                ce_midpoint = (gap_top + gap_bottom) / 2
                gap_size = max(gap_top - gap_bottom, 1e-9)
                post_highs = h[i+1:] if i+1 < len(df) else np.array([])
                max_reach = post_highs.max() if post_highs.size > 0 else 0
                if max_reach < gap_top:
                    fill_ratio = ((max_reach - gap_bottom) / gap_size) * 100
                    open_bear_fvgs.append({"top": gap_top, "bottom": gap_bottom, "ce": ce_midpoint, "fill_ratio": fill_ratio, "ce_hit": max_reach >= ce_midpoint})
        current_price = c[-1]
        active_fvg_hit = any(fvg["bottom"] <= current_price <= fvg["top"] for fvg in open_bear_fvgs)
        ce_rejected = any(fvg["ce_hit"] and current_price < fvg["ce"] for fvg in open_bear_fvgs)
        return {"fvg_valid": active_fvg_hit, "ce_rejected": ce_rejected}

    @staticmethod
    def qualify_institutional_blocks(df: pd.DataFrame, state_meta: Dict) -> Dict:
        c, o, h, l, v = df['close'].values, df['open'].values, df['high'].values, df['low'].values, df['volume'].values
        rolling_vol = df['volume'].rolling(20).mean().values
        ob_profile = {"ob_active": False, "breaker_active": False}
        if len(df) < 20 or not state_meta["dealing_high"]: return ob_profile

        for i in range(5, len(df) - 1):
            if np.isnan(rolling_vol[i]): continue
            displacement = v[i+1] > rolling_vol[i] * 1.5 and (abs(c[i+1] - o[i+1]) / (h[i+1] - l[i+1])) > 0.60
            if c[i] > o[i] and c[i+1] < l[i] and displacement:
                ob_top, ob_bottom = h[i], l[i]
                if (len(df) - i) > 60: continue
                post_highs = h[i+1:]
                touches = sum(1 for x in post_highs if ob_bottom <= x <= ob_top)
                if ob_bottom <= c[-1] <= ob_top and touches <= 3: ob_profile["ob_active"] = True
                if state_meta["dealing_high"] > ob_top and c[-1] < ob_bottom: ob_profile["breaker_active"] = True
        return ob_profile

class ProductionRiskManager:
    @staticmethod
    def generate_protected_boundaries(df: pd.DataFrame, direction: str, entry: float, state_meta: Dict) -> Tuple[float, float, float]:
        high_low = df['high'] - df['low']
        high_cp = np.abs(df['high'] - df['close'].shift())
        low_cp = np.abs(df['low'] - df['close'].shift())
        tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        if pd.isna(atr) or atr <= 0: atr = float(df['close'].std() * 0.1 if df['close'].std() > 0 else entry * 0.001)
        if direction == "SHORT":
            sl = max(state_meta["dealing_high"], entry + (atr * 1.5))
            tp = entry - (abs(sl - entry) * 3.0)
        else:
            sl = min(state_meta["dealing_low"], entry - (atr * 1.5))
            tp = entry + (abs(entry - sl) * 3.0)
        return float(sl), float(tp), float(atr)

class SessionKillzoneFilter:
    @staticmethod
    def check_killzone() -> Tuple[bool, str]:
        current_hour_utc = datetime.now(timezone.utc).hour
        if 7 <= current_hour_utc <= 10: return True, "LONDON_OPEN"
        elif 12 <= current_hour_utc <= 15: return True, "NEW_YORK_OPEN"
        elif 0 <= current_hour_utc <= 3: return True, "ASIA_ACCUMULATION"
        return False, "OUTSIDE_KILLZONE"

# ========================================================
# 5. EXECUTION PIPELINE PROCESSING ENVIRONMENT
# ========================================================
class CompleteSentinelEngine:
    def __init__(self):
        self.db = AdvancedSignalDatabase()
        self.structure_engine = StructuralStateEngine()

    async def process_market_execution(self, symbol: str, exchange: ccxt.Exchange):
        allowed, session_name = SessionKillzoneFilter.check_killzone()
        if not allowed: return

        try:
            ltf_ohlcv = await exchange.fetch_ohlcv(symbol, '15m', limit=150)
            htf_ohlcv = await exchange.fetch_ohlcv(symbol, '4h', limit=50)
            if len(ltf_ohlcv) < 30 or len(htf_ohlcv) < 20: return
            
            df_ltf = pd.DataFrame(ltf_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_htf = pd.DataFrame(htf_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            df_ltf, state_meta = self.structure_engine.map_market_geometry(df_ltf)
            closes, volumes = df_ltf['close'].values, df_ltf['volume'].values
            vol_ma = df_ltf['volume'].rolling(20).mean().values
            vol_valid = volumes[-1] > vol_ma[-1] * 1.5 if not np.isnan(vol_ma[-1]) else False
            
            true_bear_choch = False
            if state_meta["last_hl"] and closes[-1] < state_meta["last_hl"] and vol_valid: true_bear_choch = True
                
            liquidity = TrueLifecycleScanner.calculate_clustered_liquidity(df_ltf)
            true_bear_sweep = False
            if liquidity["EQH"]:
                max_eqh_target = max(liquidity["EQH"])
                if df_ltf['high'].iloc[-1] > max_eqh_target and closes[-1] < max_eqh_target: true_bear_sweep = True
                    
            fvg_profile = TrueLifecycleScanner.scan_fvg_depth_profiles(df_ltf)
            ob_profile = TrueLifecycleScanner.qualify_institutional_blocks(df_ltf, state_meta)
            equilibrium = (state_meta["dealing_high"] + state_meta["dealing_low"]) / 2
            in_premium_pricing = closes[-1] > equilibrium
            
            calibrated_weights = self.db.query_bayesian_weights()
            features_matrix = {
                "bias": 1 if in_premium_pricing else 0, "sweep": 1 if true_bear_sweep else 0,
                "choch": 1 if (true_bear_choch or state_meta["bos_confirmed"]) else 0,
                "ob": 1 if (ob_profile["ob_active"] or ob_profile["breaker_active"]) else 0,
                "fvg": 1 if (fvg_profile["fvg_valid"] or fvg_profile["ce_rejected"]) else 0
            }
            
            confidence_index = (features_matrix["bias"] * calibrated_weights["BIAS"] +
                                features_matrix["sweep"] * calibrated_weights["SWEEP"] +
                                features_matrix["choch"] * calibrated_weights["CHOCH"] +
                                features_matrix["ob"] * calibrated_weights["OB"] +
                                features_matrix["fvg"] * calibrated_weights["FVG"])

            if confidence_index >= 65.0:
                entry = float(closes[-1])
                sl, tp, atr_val = ProductionRiskManager.generate_protected_boundaries(df_ltf, "SHORT", entry, state_meta)
                self.db.log_trade_intent(symbol, "SHORT", entry, sl, tp, confidence_index, features_matrix)
                
                alert_layout = (
                    f"🚨 *[QUANTUM INSTITUTIONAL VECTOR TRIGGER]* 🚨\n"
                    f"========================================\n"
                    f"• *Symbol Coordinate:* `{symbol}`\n"
                    f"• *Execution Order:* `SHORT DISPLACEMENT (SELL)`\n"
                    f"• *Geometric Entry Base:* `{entry}`\n"
                    f"• *Stop Loss Matrix Zone:* `{sl:.2f}`\n"
                    f"• *Take Profit Target R3:* `{tp:.2f}`\n"
                    f"----------------------------------------\n"
                    f"📊 *Bayesian Confidence Resolve:* `{confidence_index:.2f}%` / 100%\n"
                    f"========================================\n"
                )
                await send_telegram_alert(alert_layout)
        except Exception as err:
            print(f"❌ [PIPELINE FAULT] Token {symbol} iteration dropped: {str(err)}")

    async def engine_core_loop(self):
        # Dual operational loops for ambient monitoring
        okx_client = ccxt.okx({"enableRateLimit": True})
        gate_client = ccxt.gate({"enableRateLimit": True})
        
        await send_telegram_alert(" eagles *The Quantum-Sentinel V8 Dual-Core Framework (OKX & Gate.io) is now running on Render Production Cloud. Listening...*")
        
        try:
            while True:
                for symbol in list(global_state.tracked_symbols):
                    # Ambient system routes tracking through default loops
                    await self.process_market_execution(symbol, okx_client)
                await asyncio.sleep(300)
        except Exception as loop_err:
            print(f"💥 Main Runtime Loop Broken: {str(loop_err)}")
        finally:
            await okx_client.close()
            await gate_client.close()

if __name__ == "__main__":
    port_allocated = int(os.environ.get("PORT", 10000))
    print(f"📡 System Engine Binding Server Proxy onto Port: {port_allocated}")
    uvicorn.run("bot:app", host="0.0.0.0", port=port_allocated, workers=1)
