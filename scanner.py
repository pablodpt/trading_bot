import os
import time
from datetime import datetime
from dotenv import load_dotenv
import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ================= CARGAR CREDENCIALES =================
load_dotenv()  # Carga local desde .env

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    raise SystemExit(
        "❌ ERROR: Credenciales de Telegram no encontradas.\n"
        "Asegúrate de configurar TELEGRAM_TOKEN y TELEGRAM_CHAT_ID en GitHub Secrets o en tu archivo .env."
    )

TICKERS_SP500 = [
    "MSFT", "NVDA", "GOOGL", "AMD",
    "JPM", "V", "WMT", "XOM", "COST", 
    "JNJ", "CRM", "TSM", "ASML", "MU", 
    "SPY", "QQQ", "GLD", "TLT"
]

class TradingAgentScanner:
    def __init__(self, capital=10000):
        self.capital = capital
        self.token = TELEGRAM_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID

    def enviar_alerta(self, mensaje):
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            resp = requests.post(
                url,
                data={
                    "chat_id": self.chat_id,
                    "text": mensaje,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                },
                timeout=10
            )
            if resp.status_code != 200:
                print(f"⚠️ Error Telegram (Status {resp.status_code}): {resp.text}", flush=True)
            else:
                print("📱 Alerta enviada a Telegram con éxito ✅", flush=True)
        except Exception as e:
            print(f"⚠️ Fallo al conectar con Telegram: {e}", flush=True)

    # ================= INDICADORES OPTIMIZADOS =================
    def rsi(self, precios, periodo=14):
        """Calcula el RSI utilizando la fórmula oficial de suavización de Wilder (EWM)."""
        delta = precios.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        
        avg_gain = gain.ewm(alpha=1/periodo, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/periodo, adjust=False).mean()
        
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def atr(self, df, periodo=14):
        hl = df["High"] - df["Low"]
        hc = (df["High"] - df["Close"].shift()).abs()
        lc = (df["Low"] - df["Close"].shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return tr.ewm(alpha=1/periodo, adjust=False).mean()

    # ================= DESCARGA EN LOTE (BATCH) =================
    def descargar_lote(self, tickers):
        """Descarga todos los tickers en una sola petición masiva a yfinance."""
        print(f"📥 Descargando datos para {len(tickers)} tickers...", flush=True)
        try:
            datos = yf.download(
                tickers, 
                period="1y", 
                group_by="ticker", 
                auto_adjust=True, 
                progress=False, 
                threads=True
            )
            return datos
        except Exception as e:
            print(f"⚠️ Error en descarga masiva: {e}", flush=True)
            return None

    # ================= ANÁLISIS POR TICKER =================
    def analizar_df(self, ticker, df):
        df = df.dropna(subset=["Close"])
        if df.empty or len(df) < 200:
            return None

        df = df.copy()
        
        # Indicadores
        df["SMA50"] = df["Close"].rolling(50).mean()
        df["SMA200"] = df["Close"].rolling(200).mean()
        df["RSI2"] = self.rsi(df["Close"], 2)
        df["RSI14"] = self.rsi(df["Close"], 14)
        df["ATR"] = self.atr(df)
        
        ema_r = df["Close"].ewm(span=12, adjust=False).mean()
        ema_l = df["Close"].ewm(span=26, adjust=False).mean()
        df["MACD"] = ema_r - ema_l
        df["MACD_s"] = df["MACD"].ewm(span=9, adjust=False).mean()
        
        media = df["Close"].rolling(20).mean()
        std = df["Close"].rolling(20).std()
        df["BB_inf"] = media - 2 * std
        df["Vol_prom"] = df["Volume"].rolling(20).mean()
        df["Momentum"] = df["Close"].pct_change(126)  # ~6 meses

        u = df.iloc[-1]

        # Extracción segura de escalares
        close_val = float(u["Close"])
        sma200_val = float(u["SMA200"]) if pd.notna(u["SMA200"]) else None
        sma50_val = float(u["SMA50"]) if pd.notna(u["SMA50"]) else None
        atr_val = float(u["ATR"]) if pd.notna(u["ATR"]) else None
        vol_prom_val = float(u["Vol_prom"]) if pd.notna(u["Vol_prom"]) else 0

        if sma200_val is None or atr_val is None:
            return None

        # Filtro de liquidez ($10M negociados al día)
        if close_val * vol_prom_val < 10_000_000:
            return None

        macd_val = float(u["MACD"])
        macd_s_val = float(u["MACD_s"])
        macd_prev = float(df["MACD"].iloc[-2])
        macd_s_prev = float(df["MACD_s"].iloc[-2])

        rsi2_val = float(u["RSI2"])
        rsi14_val = float(u["RSI14"])
        bb_inf_val = float(u["BB_inf"])
        momentum_val = float(u["Momentum"])
        volume_val = float(u["Volume"])

        max55_prev = float(df["High"].rolling(55).max().iloc[-2])

        # --- SCORING MULTI-CONFLUENCIA ---
        score = 0
        razones = []

        if close_val > sma200_val and sma50_val > sma200_val:
            score += 2; razones.append("Tendencia alcista (Golden Cross)")
        elif close_val > sma200_val:
            score += 1; razones.append("Sobre SMA200")
        else:
            score -= 2; razones.append("Bajo SMA200")

        if macd_val > macd_s_val and macd_prev <= macd_s_prev:
            score += 2; razones.append("⚡ Cruce MACD alcista (FRESCO)")
        elif macd_val > macd_s_val:
            score += 1; razones.append("MACD alcista")
        else:
            score -= 1

        if rsi2_val < 5:
            score += 2; razones.append("Sobreventa extrema RSI2")
        if rsi14_val < 30:
            score += 1; razones.append("RSI14 sobreventa")

        if close_val < bb_inf_val:
            score += 1; razones.append("Rebote Bollinger inferior")

        if momentum_val > 0.15:
            score += 1; razones.append(f"Momentum 6m fuerte ({momentum_val*100:.0f}%)")

        if volume_val > 1.5 * vol_prom_val:
            score += 1; razones.append("Volumen 1.5x superior al promedio")

        if close_val > max55_prev:
            score += 2; razones.append("⚡ BREAKOUT nuevo máximo 55d (FRESCO)")

        # --- SEÑAL FINAL ---
        if score >= 7:
            senal = "🟢 COMPRA FUERTE"
        elif score >= 5:
            senal = "🟢 COMPRA"
        elif score <= -1:
            senal = "🔴 VENTA"
        else:
            senal = None

        return {
            "ticker": ticker, "score": score, "senal": senal,
            "precio": close_val, "razones": razones,
            "stop": close_val - 2.5 * atr_val,
            "target": close_val + 2.5 * atr_val,
            "rsi14": rsi14_val
        }

    # ================= SCANNER CON ALERTAS =================
    def escanear_mercado(self, tickers, alertar=True):
        print(f"\n🔍 Escaneando {len(tickers)} tickers... {datetime.now():%H:%M}", flush=True)
        datos_masivos = self.descargar_lote(tickers)
        
        if datos_masivos is None:
            print("❌ Error al recuperar datos de mercado.", flush=True)
            return []

        señales = []
        for ticker in tickers:
            try:
                # Extracción del DataFrame correspondiente al ticker según la estructura devuelta por yfinance
                df_ticker = datos_masivos[ticker] if len(tickers) > 1 else datos_masivos
                resultado = self.analizar_df(ticker, df_ticker)
                if resultado and resultado["senal"]:
                    señales.append(resultado)
            except Exception as e:
                print(f"⚠️ Error procesando {ticker}: {e}", flush=True)

        # Ordenar por score descendente
        señales.sort(key=lambda x: x["score"], reverse=True)

        # Reporte en consola
        print(f"\n{'='*55}\n📋 RESULTADOS — {datetime.now():%d/%m/%Y %H:%M}", flush=True)
        if not señales:
            print("   Sin señales. Mercado neutral.", flush=True)
        for s in señales:
            print(f"{s['senal']} {s['ticker']} | Score {s['score']} | ${s['precio']:.2f} "
                  f"| Stop: ${s['stop']:.2f} | Target: ${s['target']:.2f}", flush=True)

        # 📱 ALERTA A TELEGRAM
        if alertar:
            self.alertar_señales(señales, total_escaneados=len(tickers))

        return señales

    def alertar_señales(self, señales, total_escaneados=0):
        fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
        
        # CASO 1: Existen oportunidades de compra / venta
        if señales:
            msg = (f"🤖 <b>OX ALPHA — ALERTA DE TRADING</b>\n"
                   f"🕐 {fecha}\n"
                   f"🔍 {len(señales)} señales detectadas ({total_escaneados} activos)\n")

            for s in señales[:10]:
                msg += (f"\n{s['senal']} <b>{s['ticker']}</b> — Score {s['score']}/11\n"
                        f"   💰 Precio: ${s['precio']:.2f}\n"
                        f"   🛑 Stop: ${s['stop']:.2f} | 🎯 Target: ${s['target']:.2f}\n")
                for r in s["razones"][:3]:
                    msg += f"   • {r}\n"

            msg += ("\n⚠️ <i>Gestión de riesgo: máx 2% por operación. "
                    "No es asesoría financiera.</i>")
        
        # CASO 2: Mercado neutro (confirmación de funcionamiento)
        else:
            msg = (f"🤖 <b>OX ALPHA — REPORTE DE MERCADO</b>\n"
                   f"🕐 {fecha}\n"
                   f"🔍 Escaneo completado ({total_escaneados} activos).\n"
                   f"ℹ️ <i>Sin señales operables. Mercado neutro o sin confluencia.</i>")

        self.enviar_alerta(msg)


if __name__ == "__main__":
    agente = TradingAgentScanner(capital=10000)
    agente.escanear_mercado(TICKERS_SP500, alertar=True)