import os
import time
import pandas as pd
import numpy as np
from binance.client import Client
from binance.exceptions import BinanceAPIException
from dotenv import load_dotenv

# Carregar variáveis do .env
load_dotenv()

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
USE_TESTNET = os.getenv("USE_TESTNET", "false").lower() == "true"

SYMBOL = os.getenv("SYMBOL", "ADAUSDT")
INITIAL_QUANTITY = float(os.getenv("QUANTITY", "10"))  # quantidade inicial
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
RSI_LOW = int(os.getenv("RSI_LOW", "40"))
RSI_HIGH = int(os.getenv("RSI_HIGH", "70"))
INTERVAL = os.getenv("INTERVAL", "1m")
LOOP_SLEEP_SEC = int(os.getenv("LOOP_SLEEP_SEC", "15"))
MAX_NOTIONAL = float(os.getenv("MAX_NOTIONAL_USDT", "10"))
REINVESTMENT_PERCENT = float(os.getenv("REINVESTMENT_PERCENT", "10"))  # % de reinvestimento

# Inicializar cliente
if USE_TESTNET:
    client = Client(API_KEY, API_SECRET, testnet=True)
else:
    client = Client(API_KEY, API_SECRET)

# Determinar base e quote assets
if SYMBOL.endswith('USDT'):
    base_asset = SYMBOL[:-4]
    quote_asset = 'USDT'
else:
    base_asset = SYMBOL[3:]
    quote_asset = SYMBOL[:3]

# Variáveis globais para reinvestimento
current_quantity = INITIAL_QUANTITY
entry_price = 0
trade_count = 0
profit_total = 0

def get_klines(symbol, interval, lookback=100):
    try:
        klines = client.get_klines(symbol=symbol, interval=interval, limit=lookback)
        closes = [float(k[4]) for k in klines]
        return closes
    except BinanceAPIException as e:
        print(f"Erro API Binance: {e}")
        return []

def calculate_rsi(prices, period=14):
    if len(prices) < period:
        return None

    deltas = np.diff(prices)
    gains = deltas.clip(min=0)
    losses = -deltas.clip(max=0)
    rsi_values = []

    for i in range(period, len(prices)):
        gain = np.mean(gains[i - period:i])
        loss = np.mean(losses[i - period:i])
        rs = gain / loss if loss > 0 else 0
        rsi = 100 - (100 / (1 + rs))
        rsi_values.append(rsi)

    return rsi_values[-1] if rsi_values else None

def place_order(side, quantity, symbol):
    try:
        # Arredonda a quantidade para 2 casas decimais (Binance requirement)
        quantity = round(quantity, 2)
        order = client.create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity
        )
        print(f"✅ Ordem enviada: {side} {quantity} {symbol}")
        return order
    except BinanceAPIException as e:
        print(f"Erro ao enviar ordem: {e}")
        return None

def get_balance(asset):
    try:
        balance = client.get_asset_balance(asset=asset)
        return float(balance["free"]) if balance else 0.0
    except BinanceAPIException as e:
        print(f"Erro ao consultar saldo: {e}")
        return 0.0

def calculate_profit(entry_price, exit_price, quantity):
    """Calcula o lucro de uma operação"""
    gross_profit = (exit_price - entry_price) * quantity
    fees = (entry_price * quantity * 0.001) + (exit_price * quantity * 0.001)
    net_profit = gross_profit - fees
    return net_profit

def adjust_quantity_based_on_profit(net_profit, current_qty, last_price):
    """Ajusta a quantidade para a próxima operação baseado no lucro"""
    if net_profit > 0:
        # Reinveste 10% do lucro
        additional_qty = (net_profit * (REINVESTMENT_PERCENT / 100)) / last_price
        new_qty = current_qty + additional_qty
        print(f"🎯 Lucro de ${net_profit:.4f}! Nova quantidade: {new_qty:.4f} {base_asset}")
        return new_qty
    else:
        # Se prejuízo, mantém a quantidade atual
        print(f"⚠ Prejuízo de ${abs(net_profit):.4f}. Mantendo quantidade: {current_qty:.4f}")
        return current_qty

def main():
    global current_quantity, entry_price, trade_count, profit_total
    
    # Verifica se já está em posição ao iniciar
    print("🔍 Verificando posição existente...")
    initial_balance = get_balance(base_asset)
    
    if initial_balance >= 0.1:  # Pelo menos 0.1 ADA
        print(f"🎯 Posição existente detectada! {initial_balance} {base_asset} disponíveis")
        in_position = True
        # AJUSTE CRÍTICO: Usa o saldo real em vez da quantidade fixa
        current_quantity = initial_balance
        print(f"🔧 Quantidade ajustada para: {current_quantity:.4f} {base_asset}")
        
        # Usa o preço médio atual como entrada estimada
        closes = get_klines(SYMBOL, INTERVAL, 10)
        if closes:
            entry_price = sum(closes[-5:]) / 5  # Preço médio das últimas 5 velas
            print(f"📊 Preço de entrada estimado: ${entry_price:.6f}")
        else:
            entry_price = float(input(f"💡 Digite o preço de entrada aproximado para {base_asset}: "))
    else:
        in_position = False
        print("🆕 Iniciando sem posição existente")

    while True:
        closes = get_klines(SYMBOL, INTERVAL, 100)
        if len(closes) == 0:
            time.sleep(LOOP_SLEEP_SEC)
            continue

        rsi = calculate_rsi(closes, RSI_PERIOD)
        if rsi is None:
            print("RSI insuficiente, aguardando...")
            time.sleep(LOOP_SLEEP_SEC)
            continue

        last_price = closes[-1]
        print(f"Preço: {last_price:.6f} | RSI: {rsi:.2f} | Qtd: {current_quantity:.4f} | Trades: {trade_count} | Lucro Total: ${profit_total:.4f}")

        # Compra da base asset usando quote asset
        if rsi < RSI_LOW and not in_position:
            quote_balance = get_balance(quote_asset)
            notional = last_price * current_quantity
            
            if quote_balance >= notional and notional <= MAX_NOTIONAL:
                print(f"📉 RSI baixo! Comprando {current_quantity:.4f} {base_asset}...")
                order = place_order("BUY", current_quantity, SYMBOL)
                
                if order:
                    print("🔄 Aguardando atualização de saldo...")
                    time.sleep(3)
                    new_balance = get_balance(base_asset)
                    print(f"✅ Saldo atualizado de {base_asset}: {new_balance}")
                    
                    entry_price = last_price
                    in_position = True
                    trade_count += 1
                    print(f"✅ Compra executada! Entrada: ${entry_price:.6f}")
            else:
                print(f"⚠ Saldo insuficiente em {quote_asset}. Necessário: {notional:.6f} {quote_asset}, Disponível: {quote_balance:.6f} {quote_asset}")

        # Venda da base asset para quote asset
        elif rsi > RSI_HIGH and in_position:
            time.sleep(2)
            base_balance = get_balance(base_asset)
            print(f"🔍 Debug Venda - Saldo {base_asset}: {base_balance}, Necessário: {current_quantity}")
            
            if base_balance >= current_quantity * 0.99:  # Permite 1% de tolerância
                print(f"📈 RSI alto! Vendendo {current_quantity:.4f} {base_asset}...")
                order = place_order("SELL", current_quantity, SYMBOL)
                
                if order:
                    net_profit = calculate_profit(entry_price, last_price, current_quantity)
                    profit_total += net_profit
                    
                    current_quantity = adjust_quantity_based_on_profit(net_profit, current_quantity, last_price)
                    
                    in_position = False
                    print(f"✅ Venda concluída! Lucro: ${net_profit:.4f}")
            else:
                print(f"⚠ Saldo insuficiente em {base_asset}. Tentando novamente...")
                time.sleep(2)

        time.sleep(LOOP_SLEEP_SEC)

if __name__ == "__main__":
    main()