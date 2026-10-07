import yfinance as yf
import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime
import os
import requests
import time
from datetime import datetime
from dotenv import load_dotenv

# ================= CARGAR CREDENCIALES =================
load_dotenv()  # Lee el archivo .env automáticamente

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# --- Validación: falla rápido si falta algo ---
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    raise SystemExit(
        "❌ ERROR: Credenciales de Telegram no encontradas.\n"
        "   1. Crea un archivo .env en la misma carpeta\n"
        "   2. Añade TELEGRAM_TOKEN y TELEGRAM_CHAT_ID\n"
        "   Ejemplo:\n"
        "   TELEGRAM_TOKEN=123456:ABC-DEF...\n"
        "   TELEGRAM_CHAT_ID=123456789"
    )

class TradingAgentScanner:
    def __init__(self, capital=10000):
        self.capital = capital
        self.token = TELEGRAM_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID

    def enviar_alerta(self, mensaje):
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

            resp = requests.post(url, data={
                "chat_id": self.chat_id,
                "text": mensaje,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }, timeout=10)
            if resp.status_code != 200:
                print(f"⚠️ Error Telegram: {resp.text}")
        except Exception as e:
            print(f"⚠️ Fallo envío: {e}")

    # ... el resto de la clase igual que antes ...




TICKERS_SP500 = [
    "MSFT", "NVDA", "GOOGL","AMD",
    "JPM", "V", "WMT", "XOM", "COST", 
    "JNJ","CRM",
    "TSM", "ASML",  "MU","SPY", "QQQ","GLD","TLT"
    # ... agrega los que quieras (pueden ser cientos)
]



    # ================= INDICADORES (compactos) =================
    def rsi(self, precios, periodo=14):
        delta = precios.diff()
        g = delta.clip(lower=0).rolling(periodo).mean()
        p = -delta.clip(upper=0).rolling(periodo).mean()
        return 100 - 100 / (1 + g / p)

    def atr(self, df, periodo=14):
        hl = df["High"] - df["Low"]
        hc = (df["High"] - df["Close"].shift()).abs()
        lc = (df["Low"] - df["Close"].shift()).abs()
        return pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(periodo).mean()

    def descargar(self, ticker):
        try:
            df = yf.download(ticker, period="1y", auto_adjust=True,
                             progress=False, threads=True)
            if df.empty or len(df) < 250:
                return None
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            return df
        except Exception:
            return None

    # ================= ANÁLISIS =================
    def analizar(self, ticker):
        df = self.descargar(ticker)
        if df is None:
            return None

        # Indicadores
        df["SMA50"] = df["Close"].rolling(50).mean()
        df["SMA200"] = df["Close"].rolling(200).mean()
        df["RSI2"] = self.rsi(df["Close"], 2)
        df["RSI14"] = self.rsi(df["Close"], 14)
        df["ATR"] = self.atr(df)
        ema_r = df["Close"].ewm(span=12).mean()
        ema_l = df["Close"].ewm(span=26).mean()
        df["MACD"] = ema_r - ema_l
        df["MACD_s"] = df["MACD"].ewm(span=9).mean()
        media = df["Close"].rolling(20).mean()
        std = df["Close"].rolling(20).std()
        df["BB_inf"] = media - 2 * std
        df["BB_sup"] = media + 2 * std
        df["Vol_prom"] = df["Volume"].rolling(20).mean()
        df["Momentum"] = df["Close"].pct_change(126)  # 6 meses

        u = df.iloc[-1]
        if pd.isna(u["SMA200"]) or pd.isna(u["ATR"]):
            return None

        # Filtro de liquidez: volumen mínimo $10M/día
        if u["Close"] * u["Vol_prom"] < 10_000_000:
            return None

        # --- SCORING MULTI-CONFLUENCIA ---
        score = 0
        razones = []

        if u["Close"] > u["SMA200"] and u["SMA50"] > u["SMA200"]:
            score += 2; razones.append("Tendencia alcista (Golden Cross)")
        elif u["Close"] > u["SMA200"]:
            score += 1; razones.append("Sobre SMA200")
        else:
            score -= 2; razones.append("Bajo SMA200")

        if u["MACD"] > u["MACD_s"] and df["MACD"].iloc[-2] <= df["MACD_s"].iloc[-2]:
            score += 2; razones.append("⚡ Cruce MACD alcista (FRESCO)")
        elif u["MACD"] > u["MACD_s"]:
            score += 1; razones.append("MACD alcista")
        else:
            score -= 1

        if u["RSI2"] < 5:
            score += 2; razones.append("Sobreventa extrema RSI2")
        if u["RSI14"] < 30:
            score += 1; razones.append("RSI14 sobreventa")

        if u["Close"] < u["BB_inf"]:
            score += 1; razones.append("Rebote Bollinger inferior")

        if u["Momentum"] > 0.15:
            score += 1; razones.append(f"Momentum 6m fuerte ({u['Momentum']*100:.0f}%)")

        if u["Volume"] > 1.5 * u["Vol_prom"]:
            score += 1; razones.append("Volumen 1.5x superior al promedio")

        if u["Close"] > u["High"].rolling(55).max().iloc[-2]:
            score += 2; razones.append("⚡ BREAKOUT nuevo máximo 55d (FRESCO)")

        # --- SEÑAL FINAL ---
        if score >= 7:
            senal = "🟢 COMPRA FUERTE"
        elif score >= 5:
            senal = "🟢 COMPRA"
        elif score <= -1:
            senal = "🔴 VENTA"
        else:
            senal = None  # No alertar señales neutrales

        return {
            "ticker": ticker, "score": score, "senal": senal,
            "precio": u["Close"], "razones": razones,
            "stop": u["Close"] - 2.5 * u["ATR"],
            "target": u["Close"] + 2.5 * u["ATR"],
            "rsi14": u["RSI14"]
        }

    # ================= SCANNER CON ALERTAS =================
    def escanear_mercado(self, tickers, alertar=True):
        print(f"\n🔍 Escaneando {len(tickers)} tickers... {datetime.now():%H:%M}")
        señales = []
        procesados = 0

        for i, ticker in enumerate(tickers, 1):
            resultado = self.analizar(ticker)
            procesados += 1
            if procesados % 10 == 0:
                print(f"   ⏳ Progreso: {procesados}/{len(tickers)}")

            if resultado and resultado["senal"]:
                señales.append(resultado)

            # Pausa anti-bloqueo de la API cada 20 tickers
            if i % 20 == 0:
                time.sleep(1)

        # Ordenar por score
        señales.sort(key=lambda x: x["score"], reverse=True)

        # Reporte en consola
        print(f"\n{'='*55}\n📋 RESULTADOS — {datetime.now():%d/%m/%Y %H:%M}")
        if not señales:
            print("   Sin señales. Mercado neutral.")
        for s in señales:
            print(f"{s['senal']} {s['ticker']} | Score {s['score']} | ${s['precio']:.2f} "
                  f"| Stop s[′stop′]:.2f∣Target{s['stop']:.2f} | Targets[′stop′]:.2f∣Target{s['target']:.2f}")

        # 📱 ALERTA A TELEGRAM
        if alertar and señales:
            self.alertar_señales(señales)

        return señales

    def alertar_señales(self, señales):
        fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
        msg = (f"🤖 <b>OX ALPHA — ALERTA DE TRADING</b>\n"
               f"🕐 {fecha}\n"
               f"🔍 {len(señales)} señales detectadas\n")

        for s in señales[:10]:  # Máximo 10 por mensaje
            msg += (f"\n{s['senal']} <b>{s['ticker']}</b> — Score {s['score']}/11\n"
                    f"   💰 Precio: ${s['precio']:.2f}\n"
                    f"   🛑 Stop: s[′stop′]:.2f∣🎯Target:{s['stop']:.2f} | 🎯 Target:s[′stop′]:.2f∣🎯Target:{s['target']:.2f}\n")
            for r in s["razones"][:3]:
                msg += f"   • {r}\n"

        msg += ("\n⚠️ <i>Gestión de riesgo: máx 2% por operación. "
                "No es asesoría financiera.</i>")

        self.enviar_alerta(msg)
        print("📱 Alerta enviada a Telegram ✅")


# ================= EJECUCIÓN =================
if __name__ == "__main__":
    agente = TradingAgentScanner(capital=10000)

    # --- MODO ÚNICO: escanea una vez ---
    señales = agente.escanear_mercado(TICKERS_SP500)

    # --- MODO AUTOMÁTICO: escanea cada hora, 24/7 ---
    MODO_CONTINUO = False  # Cambia a True para vigilancia permanente

    if MODO_CONTINUO:
        agente.enviar_alerta("🤖 <b>Ox Alpha iniciado</b> — vigilancia activa ✅")
        while True:
            try:
                agente.escanear_mercado(TICKERS_SP500, alertar=True)
            except Exception as e:
                print(f"Error: {e}")
                agente.enviar_alerta(f"⚠️ Error en scanner: {e}")
            print("\n💤 Esperando 60 minutos para el próximo escaneo...")
            time.sleep(3600)
