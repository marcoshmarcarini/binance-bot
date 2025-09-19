import os
import time
import pandas as pd
import numpy as np
from binance.client import Client
from binance.exceptions import BinanceAPIException
from dotenv import load_dotenv
import requests

# Carregar variáveis do .env
load_dotenv()

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
USE_TESTNET = os.getenv("USE_TESTNET", "false").lower() == "true"

SYMBOL = os.getenv("SYMBOL", "ADAUSDT")
QUANTITY = float(os.getenv("QUANTITY", "10"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
RSI_LOW = int(os.getenv("RSI_LOW", "30"))
RSI_HIGH = int(os.getenv("RSI_HIGH", "70"))
INTERVAL = os.getenv("INTERVAL", "1m")
LOOP_SLEEP_SEC = int(os.getenv("LOOP_SLEEP_SEC", "15"))
MAX_NOTIONAL = float(os.getenv("MAX_NOTIONAL_USDT", "15"))

# Inicializar cliente
if USE_TESTNET:
    client = Client(API_KEY, API_SECRET, testnet=True, requests_params={'timeout': 30})
else:
    client = Client(API_KEY, API_SECRET, requests_params={'timeout': 30})

# Sincronizar tempo
def sync_binance_time():
    try:
        server_time = client.get_server_time()
        binance_time = server_time['serverTime']
        local_time = int(time.time() * 1000)
        time_diff = binance_time - local_time
        client.timestamp_offset = time_diff
        print(f"⏰ Tempo sincronizado: {time_diff}ms")
        return True
    except Exception as e:
        print(f"❌ Erro ao sincronizar tempo: {e}")
        return False

# Determinar assets
if SYMBOL.endswith('USDT'):
    base_asset = SYMBOL[:-4]
    quote_asset = 'USDT'
else:
    base_asset = SYMBOL[3:]
    quote_asset = SYMBOL[:3]

# ================== FUNÇÕES PRINCIPAIS ==================

def get_klines(symbol, interval, lookback=100):
    """Obtém dados de preço com múltiplas tentativas"""
    for attempt in range(3):
        try:
            klines = client.get_klines(symbol=symbol, interval=interval, limit=lookback)
            closes = [float(k[4]) for k in klines]  # preço de fechamento
            return closes
        except Exception as e:
            print(f"⚠️ Tentativa {attempt+1}/3 - Erro ao obter klines: {e}")
            time.sleep(2)
    return []

def calculate_rsi(prices, period=14):
    """Calcula RSI de forma confiável"""
    if len(prices) <= period:
        return None
    
    deltas = np.diff(prices)
    gains = deltas.clip(min=0)
    losses = -deltas.clip(max=0)
    
    # Calcula médias simples
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi

def get_balance(asset, retries=3):
    """Obtém saldo com retry"""
    for attempt in range(retries):
        try:
            balance = client.get_asset_balance(asset=asset)
            if balance and 'free' in balance:
                return float(balance['free'])
        except Exception as e:
            print(f"⚠️ Tentativa {attempt+1}/{retries} - Erro ao consultar saldo {asset}: {e}")
            time.sleep(2)
    return 0.0

def place_order(side, quantity, symbol):
    """Executa ordem com tratamento robusto de erros"""
    try:
        # Verifica se a quantidade é válida
        if quantity <= 0:
            print(f"❌ Quantidade inválida: {quantity}")
            return None
            
        order = client.create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity
        )
        print(f"✅ Ordem {side} de {quantity} {symbol} executada!")
        return order
    except BinanceAPIException as e:
        print(f"❌ Erro na ordem {side}: {e}")
        return None
    except Exception as e:
        print(f"❌ Erro geral na ordem: {e}")
        return None

def get_symbol_info(symbol):
    """Obtém informações do símbolo para lot size"""
    try:
        info = client.get_symbol_info(symbol)
        return info
    except Exception as e:
        print(f"❌ Erro ao obter info do símbolo: {e}")
        return None

def adjust_quantity(quantity, symbol_info):
    """Ajusta quantidade para as regras da Binance"""
    if not symbol_info:
        return quantity
        
    for filtro in symbol_info.get('filters', []):
        if filtro['filterType'] == 'LOT_SIZE':
            min_qty = float(filtro['minQty'])
            max_qty = float(filtro['maxQty'])
            step_size = float(filtro['stepSize'])
            
            # Arredonda para o step size
            adjusted_qty = np.floor(quantity / step_size) * step_size
            adjusted_qty = max(min_qty, min(adjusted_qty, max_qty))
            adjusted_qty = round(adjusted_qty, 8)
            
            return adjusted_qty
            
    return quantity

# ================== LÓGICA PRINCIPAL ==================

def main():
    print("⏰ Sincronizando tempo com Binance...")
    sync_binance_time()
    
    symbol_info = get_symbol_info(SYMBOL)
    adjusted_quantity = adjust_quantity(QUANTITY, symbol_info)
    
    print(f"🎯 Iniciando Bot RSI")
    print(f"📊 Par: {SYMBOL}")
    print(f"📈 Estratégia: Compra RSI < {RSI_LOW}, Venda RSI > {RSI_HIGH}")
    print(f"💼 Quantidade: {adjusted_quantity} {base_asset}")
    
    in_position = False
    entry_price = 0
    trade_count = 0
    profit_total = 0
    
    # Verifica saldo inicial
    usdt_balance = get_balance(quote_asset)
    asset_balance = get_balance(base_asset)
    
    print(f"💰 Saldo inicial: {usdt_balance} {quote_asset}, {asset_balance} {base_asset}")
    
    # Se já tem o ativo, considera em posição
    if asset_balance >= adjusted_quantity * 0.8:
        in_position = True
        print(f"🎯 Posição existente detectada: {asset_balance} {base_asset}")
        entry_price = float(input("💡 Digite o preço médio de entrada: "))
    
    while True:
        try:
            # Obtém dados de preço
            closes = get_klines(SYMBOL, INTERVAL, 100)
            if not closes:
                print("⚠️ Não foi possível obter dados, aguardando...")
                time.sleep(LOOP_SLEEP_SEC)
                continue
            
            current_price = closes[-1]
            rsi = calculate_rsi(closes, RSI_PERIOD)
            
            if rsi is None:
                print("⏳ Calculando RSI...")
                time.sleep(LOOP_SLEEP_SEC)
                continue
            
            print(f"📊 Preço: ${current_price:.6f} | RSI: {rsi:.2f} | Posição: {in_position}")
            print(f"📈 Trades: {trade_count} | Lucro Total: ${profit_total:.6f}")
            
            # VERIFICA SALDO EM TEMPO REAL
            usdt_balance = get_balance(quote_asset)
            asset_balance = get_balance(base_asset)
            
            # LÓGICA DE COMPRA
            if not in_position and rsi < RSI_LOW:
                cost = current_price * adjusted_quantity
                notional_ok = cost <= MAX_NOTIONAL
                balance_ok = usdt_balance >= cost
                
                if notional_ok and balance_ok:
                    print(f"📉 RSI BAIXO ({rsi:.2f})! Comprando {adjusted_quantity} {base_asset}...")
                    order = place_order("BUY", adjusted_quantity, SYMBOL)
                    
                    if order:
                        entry_price = current_price
                        in_position = True
                        trade_count += 1
                        print(f"✅ COMPRA REALIZADA! Entrada: ${entry_price:.6f}")
                        # Aguarda atualização de saldo
                        time.sleep(3)
                else:
                    if not notional_ok:
                        print(f"⚠️ Notional muito alto: ${cost:.2f} > ${MAX_NOTIONAL:.2f}")
                    if not balance_ok:
                        print(f"⚠️ Saldo insuficiente: ${usdt_balance:.2f} < ${cost:.2f}")
            
            # LÓGICA DE VENDA
            elif in_position and rsi > RSI_HIGH:
                if asset_balance >= adjusted_quantity * 0.8:
                    print(f"📈 RSI ALTO ({rsi:.2f})! Vendendo {adjusted_quantity} {base_asset}...")
                    order = place_order("SELL", adjusted_quantity, SYMBOL)
                    
                    if order:
                        profit = (current_price - entry_price) * adjusted_quantity
                        profit_total += profit
                        in_position = False
                        print(f"✅ VENDA REALIZADA! Lucro: ${profit:.6f}")
                        print(f"💰 Lucro Total: ${profit_total:.6f}")
                else:
                    print(f"⚠️ Saldo insuficiente para vender: {asset_balance} < {adjusted_quantity}")
            
            time.sleep(LOOP_SLEEP_SEC)
            
        except KeyboardInterrupt:
            print("\n🛑 Bot interrompido pelo usuário")
            break
        except Exception as e:
            print(f"❌ Erro inesperado: {e}")
            print("🔄 Reiniciando em 10 segundos...")
            time.sleep(10)

if __name__ == "__main__":
    main()