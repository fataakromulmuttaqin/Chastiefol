"""
Chastiefol — Analysis Configuration (USER-EDITABLE)
═══════════════════════════════════════════════════════════════

File ini berisi SEMUA parameter yang bisa diedit untuk mengubah
cara Chastiefol menganalisis market dan menghasilkan sinyal trading.

CARA PAKAI:
  1. Edit nilai di bawah sesuai preferensi kamu
  2. Simpan file
  3. Restart bot — perubahan langsung aktif

CATATAN:
  - Semua weight confluence dalam satuan POIN (total max ~100)
  - Semakin tinggi weight = semakin berpengaruh terhadap keputusan
  - Set weight = 0 untuk menonaktifkan faktor tertentu
  - Minimum confluence score harus tercapai untuk generate sinyal

═══════════════════════════════════════════════════════════════
"""


# ══════════════════════════════════════════════════════════════
# 1. CONFLUENCE SCORING WEIGHTS
# ══════════════════════════════════════════════════════════════
# Setiap faktor diberi bobot (poin). Total semua faktor = max ~100.
# Sinyal hanya di-generate jika total score >= MIN_CONFLUENCE_SCORE.
#
# Set ke 0 untuk menonaktifkan faktor tertentu.
# Naikkan untuk memberi pengaruh lebih besar.

CONFLUENCE_WEIGHTS = {
    # ── Trend & Momentum ──
    "psar_aligned":             14,   # Parabolic SAR sesuai arah signal
    "adx_trending":             12,   # ADX > threshold (market trending)
    "ema_aligned":              10,   # EMA 20 > EMA 50 (bullish) atau sebaliknya
    "macd_aligned":              8,   # MACD histogram konfirmasi arah

    # ── Market Structure (SMC) ──
    "market_structure_aligned": 14,   # Bias structure sesuai signal (BOS/CHoCH)
    "order_block_proximity":     7,   # Harga dekat Order Block
    "fvg_in_range":              5,   # Fair Value Gap di area entry
    "bos_confirmed":             5,   # Break of Structure terkonfirmasi

    # ── Oscillators ──
    "rsi_zone":                  8,   # RSI di zona favorable (not overbought/oversold)

    # ── Volume (penting untuk crypto) ──
    "volume_confirmation":      10,   # Volume spike > 1.5x average
    "mfi_aligned":               5,   # Money Flow Index konfirmasi

    # ── Fibonacci (NEW) ──
    "fibonacci_level":           7,   # Harga di dekat level Fibonacci key

    # ── Session (dikurangi untuk crypto 24/7) ──
    "session_active":            2,   # Trading di jam volume tinggi
}


# ══════════════════════════════════════════════════════════════
# 2. SIGNAL GENERATION THRESHOLDS
# ══════════════════════════════════════════════════════════════

# Minimum confluence score untuk generate sinyal (0-100)
# Semakin tinggi = semakin sedikit sinyal tapi lebih berkualitas
MIN_CONFLUENCE_SCORE = 45

# Minimum ADX untuk dianggap trending
# < threshold = ranging market, sinyal diabaikan
MIN_ADX = 18

# Minimum Risk:Reward ratio
# Trade dengan R:R di bawah ini akan di-reject
MIN_RR_RATIO = 1.5

# Minimum confidence untuk eksekusi (0.0 - 1.0)
# confidence = confluence_score / 100
MIN_CONFIDENCE = 0.50


# ══════════════════════════════════════════════════════════════
# 3. INDICATOR SETTINGS
# ══════════════════════════════════════════════════════════════

# ── EMA Periods ──
EMA_FAST = 20          # EMA cepat (short-term trend)
EMA_SLOW = 50          # EMA lambat (medium-term trend)
EMA_TREND = 100        # EMA trend (long-term context)
EMA_MAJOR = 200        # EMA major (institutional level)

# ── RSI ──
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70    # Di atas ini = overbought (hindari BUY)
RSI_OVERSOLD = 30      # Di bawah ini = oversold (hindari SELL)

# ── MACD ──
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# ── ATR (Average True Range) ──
ATR_PERIOD = 14

# ── ADX (Average Directional Index) ──
ADX_PERIOD = 14

# ── Parabolic SAR ──
PSAR_AF_START = 0.02   # Acceleration Factor start
PSAR_AF_STEP = 0.02    # AF increment
PSAR_AF_MAX = 0.20     # Maximum AF

# ── Bollinger Bands ──
BB_PERIOD = 20
BB_STD_DEV = 2.0

# ── Volume ──
VOLUME_LOOKBACK = 20   # Periode untuk hitung average volume
VOLUME_SPIKE_THRESHOLD = 1.5  # Volume > N× average = spike

# ── MFI (Money Flow Index) ──
MFI_PERIOD = 14
MFI_OVERBOUGHT = 80
MFI_OVERSOLD = 20


# ══════════════════════════════════════════════════════════════
# 4. FIBONACCI SETTINGS
# ══════════════════════════════════════════════════════════════

# Periode lookback untuk menemukan swing high/low
FIBONACCI_SWING_LOOKBACK = 50

# Level Fibonacci Retracement (0.0 = low, 1.0 = high)
FIBONACCI_RETRACEMENT_LEVELS = [0.236, 0.382, 0.5, 0.618, 0.786]

# Level Fibonacci Extension
FIBONACCI_EXTENSION_LEVELS = [1.0, 1.272, 1.618, 2.0, 2.618]

# Toleransi proximity (dalam % dari harga)
# Harga dianggap "di level Fibonacci" jika jaraknya < threshold ini
FIBONACCI_PROXIMITY_PCT = 0.3  # 0.3% dari current price

# Level Fibonacci yang paling penting (diberi bobot lebih)
FIBONACCI_KEY_LEVELS = [0.382, 0.5, 0.618]  # Golden ratio levels


# ══════════════════════════════════════════════════════════════
# 5. STOP LOSS & TAKE PROFIT
# ══════════════════════════════════════════════════════════════

# ATR multiplier untuk Stop Loss
# SL = Entry ± (ATR × multiplier)
# Semakin besar = stop lebih lebar = lebih aman tapi risiko per trade lebih besar
ATR_SL_MULTIPLIER = 2.5

# Risk:Reward target
# TP = Entry ± (SL_distance × rr_target)
RR_TARGET = 2.0

# Trailing stop — aktifkan setelah profit mencapai N× risk
TRAILING_ACTIVATION_RR = 1.0   # Aktif setelah 1:1 R:R tercapai
TRAILING_ATR_MULTIPLIER = 1.5  # Trail SL di belakang harga sejauh 1.5× ATR


# ══════════════════════════════════════════════════════════════
# 6. MARKET STRUCTURE (SMC) SETTINGS
# ══════════════════════════════════════════════════════════════

# Minimum jumlah bar untuk deteksi swing high/low
STRUCTURE_SWING_BARS = 5

# Minimum FVG size (dalam % dari harga)
# FVG yang terlalu kecil diabaikan
MIN_FVG_SIZE_PCT = 0.05  # 0.05% of price

# Maximum age of Order Block (dalam jumlah bar)
# OB yang terlalu lama diabaikan
ORDER_BLOCK_MAX_AGE = 50


# ══════════════════════════════════════════════════════════════
# 7. VOLATILITY & POSITION SIZING ADJUSTMENTS
# ══════════════════════════════════════════════════════════════

# Volatility percentile thresholds
# Digunakan untuk menyesuaikan position size
HIGH_VOLATILITY_PERCENTILE = 80   # Di atas ini = high vol → kurangi size
LOW_VOLATILITY_PERCENTILE = 20    # Di bawah ini = low vol → tambah size

# Size multiplier saat volatilitas tinggi
HIGH_VOL_SIZE_REDUCTION = 0.5     # Kurangi ke 50% saat high vol

# Size multiplier saat volatilitas rendah
LOW_VOL_SIZE_BOOST = 1.3          # Naikkan ke 130% saat low vol


# ══════════════════════════════════════════════════════════════
# 8. SESSION FILTER (untuk XAUUSD — crypto diabaikan)
# ══════════════════════════════════════════════════════════════

# Jam aktif trading XAUUSD (UTC)
# Gold paling aktif saat London + New York overlap
XAUUSD_ACTIVE_HOURS = list(range(7, 21))  # 07:00 - 21:00 UTC

# Jam volume tinggi crypto (UTC)
# Crypto 24/7 tapi ada peak hours
CRYPTO_HIGH_VOLUME_HOURS = [13, 14, 15, 16, 17, 18, 19, 20]  # EU-US overlap


# ══════════════════════════════════════════════════════════════
# 9. CATEGORY-SPECIFIC OVERRIDES
# ══════════════════════════════════════════════════════════════
# Override per kategori crypto (meme lebih ketat, large cap lebih longgar)

CATEGORY_OVERRIDES = {
    "large_cap": {
        "min_confluence": 50,
        "min_adx": 20,
        "atr_sl_multiplier": 2.0,
        "max_position_pct": 10.0,
    },
    "mid_cap": {
        "min_confluence": 45,
        "min_adx": 18,
        "atr_sl_multiplier": 2.5,
        "max_position_pct": 5.0,
    },
    "meme": {
        "min_confluence": 52,      # Lebih ketat untuk meme coins
        "min_adx": 20,
        "atr_sl_multiplier": 3.5,  # Stop lebih lebar (volatile)
        "max_position_pct": 3.0,   # Size lebih kecil
        "rr_target": 3.0,          # R:R lebih tinggi (kompensasi)
    },
    "defi": {
        "min_confluence": 48,
        "min_adx": 18,
        "atr_sl_multiplier": 2.5,
        "max_position_pct": 4.0,
    },
    "ai": {
        "min_confluence": 48,
        "min_adx": 18,
        "atr_sl_multiplier": 3.0,
        "max_position_pct": 4.0,
    },
    "layer2": {
        "min_confluence": 45,
        "min_adx": 18,
        "atr_sl_multiplier": 2.5,
        "max_position_pct": 5.0,
    },
    "gaming": {
        "min_confluence": 48,
        "min_adx": 18,
        "atr_sl_multiplier": 2.5,
        "max_position_pct": 4.0,
    },
}


# ══════════════════════════════════════════════════════════════
# 10. MULTI-TIMEFRAME (MTF) SETTINGS
# ══════════════════════════════════════════════════════════════

# Timeframes yang dianalisis untuk konfirmasi
MTF_TIMEFRAMES = ["15m", "1h", "4h", "1d"]

# Weight per timeframe (higher TF = more weight)
MTF_WEIGHTS = {
    "5m": 0.05,
    "15m": 0.10,
    "30m": 0.10,
    "1h": 0.20,
    "4h": 0.25,
    "1d": 0.20,
    "1w": 0.10,
}

# Minimum MTF alignment score untuk konfirmasi sinyal
MTF_MIN_ALIGNMENT = 60.0


# ══════════════════════════════════════════════════════════════
# HELPER: Load config as dict (used by engine)
# ══════════════════════════════════════════════════════════════

def get_config() -> dict:
    """
    Get all analysis config as a single dictionary.
    Used internally by CryptoAnalysisEngine and ConfluenceScorer.
    """
    return {
        "confluence_weights": CONFLUENCE_WEIGHTS,
        "min_confluence_score": MIN_CONFLUENCE_SCORE,
        "min_adx": MIN_ADX,
        "min_rr_ratio": MIN_RR_RATIO,
        "min_confidence": MIN_CONFIDENCE,
        "ema_fast": EMA_FAST,
        "ema_slow": EMA_SLOW,
        "ema_trend": EMA_TREND,
        "ema_major": EMA_MAJOR,
        "rsi_period": RSI_PERIOD,
        "rsi_overbought": RSI_OVERBOUGHT,
        "rsi_oversold": RSI_OVERSOLD,
        "macd_fast": MACD_FAST,
        "macd_slow": MACD_SLOW,
        "macd_signal": MACD_SIGNAL,
        "atr_period": ATR_PERIOD,
        "adx_period": ADX_PERIOD,
        "psar_af_start": PSAR_AF_START,
        "psar_af_step": PSAR_AF_STEP,
        "psar_af_max": PSAR_AF_MAX,
        "bb_period": BB_PERIOD,
        "bb_std_dev": BB_STD_DEV,
        "volume_lookback": VOLUME_LOOKBACK,
        "volume_spike_threshold": VOLUME_SPIKE_THRESHOLD,
        "mfi_period": MFI_PERIOD,
        "fibonacci_swing_lookback": FIBONACCI_SWING_LOOKBACK,
        "fibonacci_retracement_levels": FIBONACCI_RETRACEMENT_LEVELS,
        "fibonacci_extension_levels": FIBONACCI_EXTENSION_LEVELS,
        "fibonacci_proximity_pct": FIBONACCI_PROXIMITY_PCT,
        "fibonacci_key_levels": FIBONACCI_KEY_LEVELS,
        "atr_sl_multiplier": ATR_SL_MULTIPLIER,
        "rr_target": RR_TARGET,
        "trailing_activation_rr": TRAILING_ACTIVATION_RR,
        "trailing_atr_multiplier": TRAILING_ATR_MULTIPLIER,
        "structure_swing_bars": STRUCTURE_SWING_BARS,
        "min_fvg_size_pct": MIN_FVG_SIZE_PCT,
        "order_block_max_age": ORDER_BLOCK_MAX_AGE,
        "high_volatility_percentile": HIGH_VOLATILITY_PERCENTILE,
        "low_volatility_percentile": LOW_VOLATILITY_PERCENTILE,
        "high_vol_size_reduction": HIGH_VOL_SIZE_REDUCTION,
        "low_vol_size_boost": LOW_VOL_SIZE_BOOST,
        "category_overrides": CATEGORY_OVERRIDES,
        "mtf_timeframes": MTF_TIMEFRAMES,
        "mtf_weights": MTF_WEIGHTS,
        "mtf_min_alignment": MTF_MIN_ALIGNMENT,
    }


def get_category_config(category: str) -> dict:
    """
    Get merged config for a specific category.
    Returns base config with category overrides applied.
    """
    base = get_config()
    overrides = CATEGORY_OVERRIDES.get(category, {})
    merged = {**base, **overrides}
    return merged
