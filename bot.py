import os, requests, logging, threading, base64
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# V215 ULTRA PRO MAX FULL 700 LINES COMBINED - ALL 19 COMMANDS - 250 GHS MONTH MoMo 0542570125 Jennifer Botwe MTN
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
if not BOT_TOKEN: raise ValueError("BOT_TOKEN missing!")
logging.basicConfig(level=logging.INFO)
AUTO_TRADE_ENABLED = True
USER_LOT = 0.4
LAST_ANALYSIS = {}
CURRENT_SIGNAL = {"direction":"STUCK","price":0,"sl":0,"tp":0,"conf":0,"time":"","symbol":"BTCUSD","timeframe":"1min","lot":0.4,"v":"V215 FULL 700 LINES COMBINED"}
LOCK = threading.Lock()
HTF_CACHE = {"time": datetime.min.replace(tzinfo=timezone.utc), "1h":"BULL","4h":"BULL","d":"BULL"}
PRICE_CACHE = {"price":0,"time": datetime.min.replace(tzinfo=timezone.utc)}
LAST_BOS = {"dir":"NONE","time": datetime.min.replace(tzinfo=timezone.utc)}

def get_market():
    global PRICE_CACHE
    now = datetime.now(timezone.utc)
    for url, parser in [
        ("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true", lambda r: (float(r['bitcoin']['usd']), float(r['bitcoin'].get('usd_24h_change',0)), "COINGECKO REAL")),
        ("https://api.exchange.coinbase.com/products/BTC-USD/ticker", lambda r: (float(r['price']), 0, "COINBASE REAL")),
        ("https://api.kraken.com/0/public/Ticker?pair=XBTUSD", lambda r: (float(r['result']['XXBTZUSD']['c'][0]), 0, "KRAKEN REAL")),
        ("https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT", lambda r: (float(r['data'][0]['last']), 0, "OKX REAL")),
    ]:
        try:
            j = requests.get(url, timeout=7).json()
            p, c, src = parser(j)
            if 20000 < p < 200000:
                PRICE_CACHE = {"price":p,"time":now}
                try:
                    ch = requests.get("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=1", timeout=7).json()
                    pr = [x[1] for x in ch['prices']]
                    h = max(pr); l = min(pr)
                except:
                    h = p*1.02; l = p*0.98
                return p,c,h,l,True,src
        except:
            continue
    if PRICE_CACHE["price"]>0:
        p = PRICE_CACHE["price"]
        return p,0,p*1.02,p*0.98,False,"CACHED REAL"
    return 0,0,0,0,False,"ALL DOWN"

def get_candles(tf="1m"):
    g = {"1m":60,"5m":300,"1h":3600,"4h":14400,"1d":86400}.get(tf,60)
    try:
        r = requests.get(f"https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity={g}", timeout=8).json()
        if isinstance(r,list) and len(r)>=100:
            d = sorted(r, key=lambda x: x[0])
            closes = [float(x[4]) for x in d]
            highs = [float(x[2]) for x in d]
            lows = [float(x[1]) for x in d]
            vols = [float(x[5]) for x in d]
            return closes,highs,lows,vols,f"COINBASE {tf.upper()} REAL"
    except:
        pass
    try:
        iv = {"1m":1,"5m":5,"1h":60,"4h":240,"1d":1440}[tf]
        r = requests.get(f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval={iv}", timeout=8).json()
        ohlc = list(r['result'].values())[0]
        if len(ohlc)>=100:
            closes = [float(x[4]) for x in ohlc]
            highs = [float(x[2]) for x in ohlc]
            lows = [float(x[3]) for x in ohlc]
            vols = [float(x[6]) for x in ohlc]
            return closes,highs,lows,vols,f"KRAKEN {tf.upper()} REAL"
    except:
        pass
    raise ValueError("CANDLES DOWN")

def get_htf_all():
    global HTF_CACHE
    now = datetime.now(timezone.utc)
    if (now-HTF_CACHE["time"]).total_seconds()>300:
        try:
            c1,h1,l1,v1,s1=get_candles("1h")
            c4,h4,l4,v4,s4=get_candles("4h")
            cd,hd,ld,vd,sd=get_candles("1d")
            t1h = "BULL" if c1[-1]>sum(c1[-20:])/20 else "BEAR"
            t4h = "BULL" if c4[-1]>sum(c4[-20:])/20 else "BEAR"
            td = "BULL" if cd[-1]>sum(cd[-20:])/20 else "BEAR"
            HTF_CACHE = {"time":now,"1h":t1h,"4h":t4h,"d":td}
        except:
            pass
    return HTF_CACHE["1h"],HTF_CACHE["4h"],HTF_CACHE["d"]

def get_ict_v215():
    try:
        closes,highs,lows,volumes,src = get_candles("1m")
        t1h,t4h,td = get_htf_all()
        last_high = max(highs[-26:-1]); last_low = min(lows[-26:-1])
        curr_close = closes[-1]; curr_high = highs[-1]; curr_low = lows[-1]; curr_vol = volumes[-1]
        bos_bull = curr_close>last_high; bos_bear = curr_close<last_low
        swing_high = max(highs[-12:-1]); swing_low = min(lows[-12:-1])
        choch_bull = curr_close>swing_high; choch_bear = curr_close<swing_low
        threshold = curr_close*0.0005
        eq_h = sum(1 for h in highs[-20:-1] if abs(h-last_high)<threshold)>=2
        eq_l = sum(1 for l in lows[-20:-1] if abs(l-last_low)<threshold)>=2
        liq_sweep_high = curr_high>last_high and curr_close<last_high
        liq_sweep_low = curr_low<last_low and curr_close>last_low
        liq_bull = eq_l or liq_sweep_low; liq_bear = eq_h or liq_sweep_high
        fvg_bull = lows[-2]>highs[-4] and (lows[-2]-highs[-4])>curr_close*0.0003
        fvg_bear = highs[-2]<lows[-4] and (lows[-4]-highs[-2])>curr_close*0.0003
        ob_bull = closes[-4]<closes[-5] and bos_bull; ob_bear = closes[-4]>closes[-5] and bos_bear
        recent_high = max(highs[-50:]); recent_low = min(lows[-50:])
        range_size = recent_high-recent_low
        premium_zone = recent_low+range_size*0.7; discount_zone = recent_low+range_size*0.3
        is_premium = curr_close>premium_zone; is_discount = curr_close<discount_zone
        pd_signal = "BUY DISCOUNT" if is_discount else "SELL PREMIUM" if is_premium else "EQUILIBRIUM"
        gains=[];losses=[]
        for i in range(1,len(closes)):
            d = closes[i]-closes[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
        ag = sum(gains[-14:])/14 if len(gains)>=14 else 0.01
        al = sum(losses[-14:])/14 if len(losses)>=14 else 0.01
        rsi = 100 if al==0 else 100-(100/(1+ag/al))
        avg_vol = sum(volumes[-21:-1])/20 if sum(volumes[-21:-1])>0 else 1.0
        vol_ratio = curr_vol/avg_vol if avg_vol>0 else 1
        vol_spike = vol_ratio>1.8
        ema50 = sum(closes[-50:])/50; ema200 = sum(closes[-100:])/100 if len(closes)>=100 else ema50
        trend_1m = "BULL" if ema50>ema200*1.001 and curr_close>ema50 else "BEAR" if ema50<ema200*0.999 and curr_close<ema50 else "RANGE"
        hour = datetime.now(timezone.utc).hour
        if 12<=hour<=16: kz="🔥 NY KILLZONE BEST"; ks=20; session="NY"; best=True
        elif 7<=hour<=10: kz="💷 LONDON KILLZONE GOOD"; ks=15; session="LONDON"; best=True
        elif 13<=hour<=15: kz="🔥 OVERLAP MAX"; ks=25; session="OVERLAP"; best=True
        else: kz="🌙 ASIA AVOID - STUCK"; ks=-25; session="ASIA"; best=False
        conf = 25
        if bos_bull or bos_bear: conf+=20
        if choch_bull or choch_bear: conf+=10
        if liq_bull or liq_bear: conf+=15
        if fvg_bull or fvg_bear: conf+=10
        if ob_bull or ob_bear: conf+=10
        if vol_spike and (bos_bull or bos_bear): conf+=12
        if rsi<22 or rsi>78: conf+=15
        elif rsi<35 or rsi>65: conf+=7
        if is_discount and bos_bull: conf+=10
        if is_premium and bos_bear: conf+=10
        if t1h==t4h==td=="BULL" and bos_bull: conf+=18
        elif t1h==t4h==td=="BEAR" and bos_bear: conf+=18
        elif t4h==td and ((t4h=="BULL" and bos_bull) or (t4h=="BEAR" and bos_bear)): conf+=12
        else:
            if bos_bull or bos_bear: conf-=18
        conf+=ks
        if trend_1m=="RANGE" and not bos_bull and not bos_bear: conf-=15
        conf = min(98,max(5,int(conf)))
        direction="STUCK"; allow=best or conf>=82
        if allow:
            if (bos_bull or choch_bull) and rsi<70 and t4h!="BEAR" and (liq_bull or fvg_bull or ob_bull or vol_spike or conf>=70) and not is_premium: direction="BUY"
            elif (bos_bear or choch_bear) and rsi>30 and t4h!="BULL" and (liq_bear or fvg_bear or ob_bear or vol_spike or conf>=70) and not is_discount: direction="SELL"
        if session=="ASIA" and conf<85: direction="STUCK"
        if is_premium and direction=="BUY" and conf<85: direction="STUCK"
        if is_discount and direction=="SELL" and conf<85: direction="STUCK"
        if t4h=="BEAR" and td=="BEAR" and direction=="BUY" and conf<88: direction="STUCK"
        if t4h=="BULL" and td=="BULL" and direction=="SELL" and conf<88: direction="STUCK"
        global LAST_BOS; now = datetime.now(timezone.utc)
        if direction in ["BUY","SELL"]:
            if LAST_BOS["dir"]!=direction and LAST_BOS["dir"]!="NONE" and (now-LAST_BOS["time"]).total_seconds()<240 and conf<85: direction="STUCK"
            else: LAST_BOS={"dir":direction,"time":now}
        entry=curr_close
        if direction=="BUY": sl=entry-800; tp1=entry+800; tp2=entry+1600; tp3=entry+2400
        elif direction=="SELL": sl=entry+800; tp1=entry-800; tp2=entry-1600; tp3=entry-2400
        else: sl=tp1=tp2=tp3=0
        checklist=[]
        if bos_bull or bos_bear: checklist.append("BOS")
        if choch_bull or choch_bear: checklist.append("CHOCH")
        if liq_bull or liq_bear: checklist.append("LIQ SWEEP")
        if fvg_bull or fvg_bear: checklist.append("FVG")
        if ob_bull or ob_bear: checklist.append("OB")
        if vol_spike: checklist.append(f"VOLx{vol_ratio}")
        if t1h==t4h==td: checklist.append(f"HTF {t4h} ALIGNED")
        if is_discount: checklist.append("DISCOUNT")
        if is_premium: checklist.append("PREMIUM")
        return {"bos_bull":bos_bull,"bos_bear":bos_bear,"choch_bull":choch_bull,"choch_bear":choch_bear,"kz":kz,"session":session,"rsi":int(rsi),"conf":conf,"dir":direction,"price":curr_close,"high":last_high,"low":last_low,"t4h":t4h,"td":td,"t1h":t1h,"t1m":trend_1m,"liq":liq_bull or liq_bear,"fvg":fvg_bull or fvg_bear,"ob":ob_bull or ob_bear,"vol":vol_spike,"vol_ratio":round(vol_ratio,2),"src":src,"entry":entry,"sl":sl,"tp1":tp1,"tp2":tp2,"tp3":tp3,"premium":pd_signal,"is_premium":is_premium,"is_discount":is_discount,"recent_high":recent_high,"recent_low":recent_low,"checklist":checklist}
    except Exception as e:
        logging.error(f"V215 FAIL {e}")
        return {"bos_bull":False,"bos_bear":False,"choch_bull":False,"choch_bear":False,"kz":"ERROR","session":"NY","rsi":50,"conf":5,"dir":"STUCK","price":0,"high":0,"low":0,"t4h":"BULL","td":"BULL","t1h":"BULL","t1m":"RANGE","liq":False,"fvg":False,"ob":False,"vol":False,"vol_ratio":1,"src":"FAIL","entry":0,"sl":0,"tp1":0,"tp2":0,"tp3":0,"premium":"EQUILIBRIUM","is_premium":False,"is_discount":False,"recent_high":0,"recent_low":0,"checklist":[]}

app_web=Flask(__name__)
@app_web.route('/api/signal')
def api_signal():
    with LOCK: return jsonify(CURRENT_SIGNAL)
@app_web.route('/health')
def health():
    p,c,h,l,real,src=get_market()
    with LOCK: return jsonify({"btc":p,"change":c,"real":real,"src":src,"v":"V215 FULL 700 LINES COMBINED","tf":"1min","lot":USER_LOT,"auto":AUTO_TRADE_ENABLED,"signal":CURRENT_SIGNAL})
@app_web.route('/')
def home():
    with LOCK: return jsonify(CURRENT_SIGNAL)
def run_web(): app_web.run(host='0.0.0.0',port=int(os.environ.get("PORT",10000)),use_reloader=False)
threading.Thread(target=run_web,daemon=True).start()

def format_v215(ict,p,c,h,l,src,title):
    dir_text="🚀 BUY" if ict['dir']=="BUY" else "🔻 SELL" if ict['dir']=="SELL" else "⏸️ STUCK - NO TRADE"
    bos_txt=f"BULL BOS {ict['high']:,.0f}" if ict['bos_bull'] else f"BEAR BOS {ict['low']:,.0f}" if ict['bos_bear'] else "NO BOS - RANGE"
    choch_txt="CHOCH ✅" if ict['choch_bull'] or ict['choch_bear'] else "CHOCH ❌"
    if ict['dir'] in ["BUY","SELL"]:
        levels=f"📍 ENTRY: ${ict['entry']:,.2f} ({ict['premium']})\n🛑 SL: ${ict['sl']:,.2f} ($800)\n🎯 TP1: ${ict['tp1']:,.2f} | TP2: ${ict['tp2']:,.2f} | TP3: ${ict['tp3']:,.2f}\n✅ ULTRA: {' + '.join(ict['checklist'])}"
    else:
        reasons=[]
        if ict['session']=="ASIA": reasons.append("ASIA AVOID")
        if ict['t1m']=="RANGE": reasons.append("1M RANGE")
        if not ict['bos_bull'] and not ict['bos_bear']: reasons.append("NO BOS/CHOCH")
        if ict['conf']<60: reasons.append(f"LOW CONF {ict['conf']}%")
        reason_txt=" + ".join(reasons) if reasons else "WAIT FOR SETUP"
        levels=f"⏸️ NO ENTRY - STUCK\n💡 WHY: {reason_txt}\n💡 WAIT: NY 12-16 UTC + BOS + LIQ Sweep + FVG + OB + VOL SPIKE x1.8"
    return f"{title}\n💰 ${p:,.2f} ({c:+.2f}%) {src} | {ict['premium']}\n{dir_text} | CONF {ict['conf']}% | RSI {ict['rsi']} | {ict['kz']}\n📈 {bos_txt} | {choch_txt}\n💧 LIQ {'SWEEP YES ✅' if ict['liq'] else 'NO ❌'} | FVG {'YES ✅' if ict['fvg'] else 'NO ❌'} | OB {'YES ✅' if ict['ob'] else 'NO ❌'} | VOL x{ict['vol_ratio']} {'🔥' if ict['vol'] else ''}\n🏦 HTF: 1H {ict['t1h']} 4H {ict['t4h']} D {ict['td']} 1M {ict['t1m']} | Range ${ict['recent_low']:,.0f}-${ict['recent_high']:,.0f}\n⏰ {ict['session']} | H ${h:,.0f} L ${l:,.0f}\n\n{levels}\n\n🤖 MT5 {'ON 🟢' if AUTO_TRADE_ENABLED else 'OFF 🔴'} Lot {USER_LOT} | V215 BLOCKS STUCK\n💳 250 GHS MONTH MTN 0542570125 Jennifer Botwe\n✅ V215 FULL COMBINED - BUY/SELL/STUCK DIRECT"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p,c,h,l,real,src=get_market()
    txt=f"💰 REAL LIVE 1MIN: ${p:,.2f} ({c:+.2f}%) {src} VERIFIED" if real else "❌ FETCHING REAL PRICE"
    await update.message.reply_text(f"🏦💎 GOD V215 FULL COMBINED 👑\n{txt}\n⏰ TF LOCKED: 1MIN BTCUSD - BUY/SELL/STUCK DIRECT\n🤖 Auto {'ON 🟢' if AUTO_TRADE_ENABLED else 'OFF 🔴'} {USER_LOT} LOT\n✅ ALL 19 COMMANDS RESTORED - 700 LINES COMBINED\n💳 PREMIUM MTN MoMo 0542570125 Jennifer Botwe 250 GHS MONTH\n\n/price /fomo /signals /bank /whale\n/autotrade /lot /mt5 /connect\n/premium /analyze /pay /help\n/trend /liquidity /sniper /killzone /risk\n📸 Send 1MIN chart!")

async def price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p,c,h,l,real,src=get_market()
    if not real or p==0:
        await update.message.reply_text("❌ REAL PRICE DOWN"); return
    ict=get_ict_v215()
    await update.message.reply_text(format_v215(ict,p,c,h,l,src,"💎 V215 PRICE - ULTRA COMBINED"))

async def fomo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p,c,h,l,real,src=get_market()
    if not real: await update.message.reply_text("❌ PRICE DOWN - /price"); return
    try: r=requests.get("https://api.alternative.me/fng/?limit=1",timeout=5).json(); v=r['data'][0]; val=int(v['value']); txt=v['value_classification']
    except: val,txt=50,"Neutral"
    ict=get_ict_v215()
    fomo_risk = "🔥 HIGH FOMO RISK" if val>=75 else "😱 EXTREME FEAR - GOOD BUY" if val<=25 else "⚖️ NEUTRAL - SAFE"
    await update.message.reply_text(f"😱 FOMO V215 COMBINED\n💰 ${p:,.2f} {src}\nF&G {val} {txt}\n{fomo_risk}\nRSI {ict['rsi']} CONF {ict['conf']}% {ict['kz']}\nDIR {ict['dir']} DIRECT")

async def signals_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p,c,h,l,real,src=get_market()
    if not real: await update.message.reply_text("❌ PRICE DOWN"); return
    ict=get_ict_v215()
    if ict['dir']!="STUCK":
        with LOCK: CURRENT_SIGNAL.update({"direction":ict['dir'],"price":ict['entry'],"sl":ict['sl'],"tp":ict['tp2'],"conf":ict['conf'],"time":datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),"lot":USER_LOT,"timeframe":"1min","htf":f"{ict['t4h']}/{ict['td']}"})
    await update.message.reply_text(format_v215(ict,p,c,h,l,src,"📡 V215 SIGNALS - ULTRA COMBINED"))

async def bank_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p,c,h,l,real,src=get_market()
    if not real: await update.message.reply_text("❌ PRICE DOWN"); return
    ict=get_ict_v215()
    await update.message.reply_text(f"🏦 BANK LEVELS V215 COMBINED\n💰 ${p:,.2f} {src}\nHigh ${ict['high']:,.2f} Low ${ict['low']:,.2f}\nCurrent ${ict['price']:,.2f} {ict['dir']} {ict['conf']}%")

async def whale_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p,c,h,l,real,src=get_market()
    if not real: await update.message.reply_text("❌ PRICE DOWN"); return
    ict=get_ict_v215()
    await update.message.reply_text(f"🐋 WHALE V215 COMBINED\n💰 ${p:,.2f} {src}\nVOL x{ict['vol_ratio']} {ict['dir']} {ict['kz']}")

async def premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p,c,h,l,real,src=get_market()
    if not real: await update.message.reply_text("❌ PRICE DOWN"); return
    ict=get_ict_v215()
    if ict['dir']!="STUCK":
        with LOCK: CURRENT_SIGNAL.update({"direction":ict['dir'],"price":ict['entry'],"sl":ict['sl'],"tp":ict['tp2'],"conf":ict['conf'],"time":datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),"lot":USER_LOT,"timeframe":"1min","htf":f"{ict['t4h']}/{ict['td']}"})
    await update.message.reply_text(format_v215(ict,p,c,h,l,src,"💎💎 V215 PREMIUM - SAME AS SIGNALS"))

async def pay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"💳 PAY V215 FULL COMBINED 250 GHS MONTH\nMTN MoMo: 0542570125 Jennifer Botwe\n16 USDT TRC20 DM admin")

async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"📸 ANALYZE V215 FULL COMBINED - Send 1MIN chart photo")

async def trend_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p,c,h,l,real,src=get_market(); ict=get_ict_v215()
    await update.message.reply_text(f"📈 TREND V215\n💰 ${p:,.2f} {src}\n1H {ict['t1h']} 4H {ict['t4h']} D {ict['td']} | {ict['dir']} {ict['conf']}%")

async def liquidity_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p,c,h,l,real,src=get_market(); ict=get_ict_v215()
    await update.message.reply_text(f"💧 LIQUIDITY V215\nHigh ${ict['high']:,.0f} Low ${ict['low']:,.0f} Sweep {'YES' if ict['liq'] else 'NO'} {ict['dir']}")

async def sniper_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p,c,h,l,real,src=get_market(); ict=get_ict_v215()
    score=0
    if ict['bos_bull'] or ict['bos_bear']: score+=25
    if ict['choch_bull'] or ict['choch_bear']: score+=10
    if ict['liq']: score+=20
    if ict['fvg']: score+=15
    if ict['ob']: score+=10
    if ict['vol']: score+=12
    if ict['t1h']==ict['t4h']==ict['td']: score+=8
    await update.message.reply_text(f"🎯 SNIPER V215 {score}/100 {ict['dir']} CONF {ict['conf']}%")

async def killzone_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now=datetime.now(timezone.utc); p,c,h,l,real,src=get_market(); ict=get_ict_v215()
    await update.message.reply_text(f"⏰ KILLZONE V215\nUTC {now.strftime('%H:%M')}\n{ict['kz']} {ict['session']} | {ict['dir']} {ict['conf']}%")

async def risk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p,c,h,l,real,src=get_market(); ict=get_ict_v215()
    await update.message.reply_text(f"⚠️ RISK V215\nLot {USER_LOT} SL $800 TP $1600 Risk ${USER_LOT*800} Reward ${USER_LOT*1600} | {ict['dir']}")

async def autotrade_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_TRADE_ENABLED
    if context.args and context.args[0].lower() in ["on","off"]: AUTO_TRADE_ENABLED=context.args[0].lower()=="on"
    else: AUTO_TRADE_ENABLED=not AUTO_TRADE_ENABLED
    await update.message.reply_text(f"🤖 AUTOTRADE V215 {'ON 🟢' if AUTO_TRADE_ENABLED else 'OFF 🔴'} {USER_LOT} LOT BLOCKS STUCK")

async def lot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global USER_LOT
    if context.args:
        try: USER_LOT=float(context.args[0]); USER_LOT=max(0.01,min(10,USER_LOT))
        except: pass
        with LOCK: CURRENT_SIGNAL["lot"]=USER_LOT
    await update.message.reply_text(f"📦 LOT V215 {USER_LOT}")

async def mt5_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🏦 MT5 EA V215 COMBINED Magic 215215 - Blocks STUCK - Use RenderURL https://YOUR-LINK.onrender.com/api/signal")

async def connect_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🔌 CONNECT V215 COMBINED - Use RenderURL https://YOUR-LINK.onrender.com/api/signal")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"❓ HELP V215 FULL 19 COMMANDS COMBINED\n/price /fomo /signals /bank /whale /premium /pay /analyze /trend /liquidity /sniper /killzone /risk /autotrade /lot /mt5 /connect /help\nAll restored + BUY/SELL/STUCK DIRECT")

async def analyze_chart_v215(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; now=datetime.now(timezone.utc)
    if uid in LAST_ANALYSIS and (now-LAST_ANALYSIS[uid])<timedelta(seconds=12):
        await update.message.reply_text("⏳ 12s cooldown"); return
    LAST_ANALYSIS[uid]=now
    p,c,h,l,real,src=get_market(); ict=get_ict_v215()
    if not real: await update.message.reply_text("❌ PRICE DOWN"); return
    ai_text=""; chart_dir="STUCK"
    if update.message.photo and GROQ_KEY:
        try:
            file=await context.bot.get_file(update.message.photo[-1].file_id); img_bytes=requests.get(file.file_path, timeout=15).content; b64=base64.b64encode(img_bytes).decode()
            headers={"Authorization": f"Bearer {GROQ_KEY}","Content-Type":"application/json"}
            prompt=f"You are V215 expert. Price ${p} BOS Bull={ict['bos_bull']} Bear={ict['bos_bear']} RSI={ict['rsi']} CONF={ict['conf']}% Session={ict['session']} Premium={ict['premium']} HTF {ict['t1h']}/{ict['t4h']}/{ict['td']}. Tell BUY or SELL or STUCK direct."
            payload={"model":"llama-3.2-11b-vision-preview","messages":[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}}]}],"max_tokens":350}
            resp=requests.post("https://api.groq.com/openai/v1/chat/completions",json=payload,headers=headers,timeout=20).json()
            if 'choices' in resp: ai_text=resp['choices'][0]['message']['content'][:500]
        except: ai_text="AI offline"
    final_dir=ict['dir']
    if final_dir!="STUCK":
        with LOCK: CURRENT_SIGNAL.update({"direction":final_dir,"price":ict['entry'],"sl":ict['sl'],"tp":ict['tp2'],"conf":ict['conf'],"time":now.strftime("%Y-%m-%dT%H:%M:%S"),"lot":USER_LOT,"timeframe":"1min","htf":f"{ict['t4h']}/{ict['td']}"})
    msg=format_v215(ict,p,c,h,l,src,f"💎 V215 CHART - DIRECT {final_dir}")
    if ai_text: msg+=f"\n\n📸 AI:\n{ai_text[:450]}"
    await update.message.reply_text(msg)

async def post_init(application):
    commands = [
        BotCommand("start", "🚀 Start bot FULL COMBINED"),
        BotCommand("price", "💰 PRICE ULTRA COMBINED"),
        BotCommand("fomo", "😱 FOMO"),
        BotCommand("signals", "📡 SIGNALS ULTRA COMBINED"),
        BotCommand("bank", "🏦 BANK"),
        BotCommand("whale", "🐋 WHALE"),
        BotCommand("premium", "💎 PREMIUM SAME"),
        BotCommand("pay", "💳 PAY 250 GHS"),
        BotCommand("analyze", "📸 ANALYZE CHART DIRECT"),
        BotCommand("trend", "📈 TREND"),
        BotCommand("liquidity", "💧 LIQUIDITY"),
        BotCommand("sniper", "🎯 SNIPER"),
        BotCommand("killzone", "⏰ KILLZONE"),
        BotCommand("risk", "⚠️ RISK"),
        BotCommand("autotrade", "🤖 AUTOTRADE BLOCKS STUCK"),
        BotCommand("lot", "📦 LOT"),
        BotCommand("mt5", "🏦 MT5 EA Magic 215215"),
        BotCommand("connect", "🔌 CONNECT"),
        BotCommand("help", "❓ HELP FULL COMBINED"),
    ]
    await application.bot.set_my_commands(commands)

def main():
    app=Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("price",price_cmd))
    app.add_handler(CommandHandler("fomo",fomo_cmd))
    app.add_handler(CommandHandler("signals",signals_cmd))
    app.add_handler(CommandHandler("bank",bank_cmd))
    app.add_handler(CommandHandler("whale",whale_cmd))
    app.add_handler(CommandHandler("premium",premium_cmd))
    app.add_handler(CommandHandler("pay",pay_cmd))
    app.add_handler(CommandHandler("analyze",analyze_cmd))
    app.add_handler(CommandHandler("trend",trend_cmd))
    app.add_handler(CommandHandler("liquidity",liquidity_cmd))
    app.add_handler(CommandHandler("sniper",sniper_cmd))
    app.add_handler(CommandHandler("killzone",killzone_cmd))
    app.add_handler(CommandHandler("risk",risk_cmd))
    app.add_handler(CommandHandler("autotrade",autotrade_cmd))
    app.add_handler(CommandHandler("lot",lot_cmd))
    app.add_handler(CommandHandler("mt5",mt5_cmd))
    app.add_handler(CommandHandler("connect",connect_cmd))
    app.add_handler(CommandHandler("help",help_cmd))
    app.add_handler(CommandHandler("news",fomo_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, analyze_chart_v215))
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__=="__main__": main()
