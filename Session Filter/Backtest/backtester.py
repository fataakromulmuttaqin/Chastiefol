"""
Chastiefol — Backtesting Engine
Walk-forward validation + Monte Carlo simulation for XAUUSD strategy.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Analysis.xauusd_engine import ChastiefollAgent, Signal, TradeSetup
from Risk.risk_manager      import (
    AccountState, DrawdownGuard, PositionSizer, GoldSessionFilter, RiskModel
)


@dataclass
class BacktestConfig:
    initial_balance:     float = 10_000.0
    risk_pct:            float = 1.0
    spread_pips:         float = 0.30     # typical XAUUSD spread
    commission_per_lot:  float = 3.50     # USD per lot round-trip
    slippage_pips:       float = 0.10
    atr_sl_mult:         float = 1.5
    rr_target:           float = 2.0
    min_confluence:      int   = 55


@dataclass
class TradeRecord:
    entry_time:  object    # timestamp
    exit_time:   object
    direction:   str
    entry_price: float
    exit_price:  float
    lot_size:    float
    pnl_usd:     float
    pnl_pips:    float
    outcome:     str       # "WIN" | "LOSS" | "BE"
    confidence:  float
    confluence:  int
    reasons:     list[str] = field(default_factory=list)


@dataclass
class BacktestResult:
    trades:             list[TradeRecord]
    final_balance:      float
    total_return_pct:   float
    total_trades:       int
    win_count:          int
    loss_count:         int
    win_rate:           float
    avg_win_usd:        float
    avg_loss_usd:       float
    profit_factor:      float
    max_drawdown_pct:   float
    sharpe_ratio:       float
    avg_rr_achieved:    float
    expectancy_usd:     float

    def summary(self) -> str:
        return (
            f"\n{'='*52}\n"
            f"  CHASTIEFOL BACKTEST RESULTS — XAUUSD\n"
            f"{'='*52}\n"
            f"  Total Trades:       {self.total_trades}\n"
            f"  Win Rate:           {self.win_rate*100:.1f}%\n"
            f"  Profit Factor:      {self.profit_factor:.2f}\n"
            f"  Max Drawdown:       {self.max_drawdown_pct:.2f}%\n"
            f"  Sharpe Ratio:       {self.sharpe_ratio:.2f}\n"
            f"  Total Return:       {self.total_return_pct:.2f}%\n"
            f"  Final Balance:      ${self.final_balance:,.2f}\n"
            f"  Avg Win:            ${self.avg_win_usd:.2f}\n"
            f"  Avg Loss:           ${self.avg_loss_usd:.2f}\n"
            f"  Expectancy/trade:   ${self.expectancy_usd:.2f}\n"
            f"  Avg R:R achieved:   {self.avg_rr_achieved:.2f}\n"
            f"{'='*52}"
        )


class Backtester:
    """
    Event-driven backtester for Chastiefol XAUUSD strategy.
    Supports:
      - Walk-forward optimization
      - Out-of-sample validation
      - Monte Carlo simulation
      - Commission + spread + slippage modeling
    """

    def __init__(self, config: BacktestConfig = None):
        self.config = config or BacktestConfig()
        self.agent  = ChastiefollAgent()
        self.sizer  = PositionSizer(
            risk_pct=self.config.risk_pct,
            model=RiskModel.FIXED_PERCENT
        )
        self.guard  = DrawdownGuard()
        self.sess   = GoldSessionFilter()

    def run(self, df: pd.DataFrame,
            lookback: int = 100,
            verbose: bool = False) -> BacktestResult:
        """
        Run backtest on historical OHLCV data.
        df must have: open, high, low, close, volume columns.
        Optional 'timestamp' column for session filtering.
        """
        config  = self.config
        balance = config.initial_balance
        peak    = balance
        trades  = []
        equity_curve = [balance]

        account = AccountState(
            balance=balance,
            equity=balance,
            peak_balance=balance,
        )

        i = lookback
        while i < len(df) - 5:
            window = df.iloc[i - lookback : i].copy()

            # Session filter
            session_active = True
            if "timestamp" in df.columns:
                ts = pd.to_datetime(df["timestamp"].iloc[i])
                session_active, _ = self.sess.is_active(ts.hour, ts.minute)

            # Risk check
            status = self.guard.check(account)
            if not status["trading_allowed"]:
                i += 1
                continue

            # Get signal from agent
            setup = self.agent.analyze(
                window,
                session_active=session_active,
                atr_multiplier_sl=config.atr_sl_mult,
                rr_target=config.rr_target,
            )

            if setup is None or setup.confidence * 100 < config.min_confluence:
                i += 1
                continue

            # Position size
            pos = self.sizer.calculate(account, setup.entry, setup.stop_loss)
            lot = round(pos.lot_size * status["size_multiplier"], 2)
            if lot < 0.01:
                i += 1
                continue

            # Apply spread + slippage to entry
            half_spread = config.spread_pips / 2
            slippage    = config.slippage_pips
            if setup.signal == Signal.BUY:
                actual_entry = setup.entry + half_spread + slippage
                actual_sl    = setup.stop_loss
                actual_tp    = setup.take_profit
            else:
                actual_entry = setup.entry - half_spread - slippage
                actual_sl    = setup.stop_loss
                actual_tp    = setup.take_profit

            # Simulate trade outcome (walk forward through future candles)
            outcome, exit_price, exit_idx = self._simulate_trade(
                df, i, setup.signal, actual_entry, actual_sl, actual_tp
            )

            # Calculate P&L (XAU: 1 lot = 100oz → $100 per $1 move)
            if setup.signal == Signal.BUY:
                pnl_raw = (exit_price - actual_entry) * lot * 100
                pnl_pips = exit_price - actual_entry
            else:
                pnl_raw  = (actual_entry - exit_price) * lot * 100
                pnl_pips = actual_entry - exit_price

            commission = config.commission_per_lot * lot
            pnl_net    = pnl_raw - commission

            # Update account
            balance += pnl_net
            account.balance = balance
            account.equity  = balance
            if pnl_net < 0:
                account.daily_loss += abs(pnl_net)
            account.update_peak()
            equity_curve.append(balance)

            if verbose:
                print(f"  [{i}] {setup.signal.value:4s} | "
                      f"E:{actual_entry:.2f} SL:{actual_sl:.2f} TP:{actual_tp:.2f} | "
                      f"Lots:{lot} | P&L: ${pnl_net:+.2f} | {outcome}")

            trades.append(TradeRecord(
                entry_time=df.index[i] if hasattr(df.index[i], 'isoformat') else i,
                exit_time=df.index[exit_idx] if hasattr(df.index[exit_idx], 'isoformat') else exit_idx,
                direction=setup.signal.value,
                entry_price=actual_entry,
                exit_price=exit_price,
                lot_size=lot,
                pnl_usd=round(pnl_net, 2),
                pnl_pips=round(pnl_pips, 2),
                outcome=outcome,
                confidence=setup.confidence,
                confluence=setup.confluence,
                reasons=setup.reasons,
            ))

            i = exit_idx + 1  # advance to after trade close

        return self._compute_stats(trades, equity_curve,
                                   self.config.initial_balance)

    def _simulate_trade(self, df, start_idx, signal,
                         entry, sl, tp) -> tuple[str, float, int]:
        """Walk bar-by-bar until SL or TP is hit."""
        for j in range(start_idx, min(start_idx + 500, len(df))):
            high = df["high"].iloc[j]
            low  = df["low"].iloc[j]

            if signal == Signal.BUY:
                if low <= sl:
                    return "LOSS", sl, j
                if high >= tp:
                    return "WIN", tp, j
            else:
                if high >= sl:
                    return "LOSS", sl, j
                if low <= tp:
                    return "WIN", tp, j

        # Timeout: close at last close price
        last_close = df["close"].iloc[min(start_idx + 499, len(df) - 1)]
        outcome = "WIN" if (
            (signal == Signal.BUY  and last_close > entry) or
            (signal == Signal.SELL and last_close < entry)
        ) else "LOSS"
        return outcome, last_close, min(start_idx + 499, len(df) - 1)

    def _compute_stats(self, trades, equity_curve,
                        initial_balance) -> BacktestResult:
        if not trades:
            return BacktestResult(
                trades=[], final_balance=initial_balance,
                total_return_pct=0, total_trades=0,
                win_count=0, loss_count=0, win_rate=0,
                avg_win_usd=0, avg_loss_usd=0,
                profit_factor=0, max_drawdown_pct=0,
                sharpe_ratio=0, avg_rr_achieved=0, expectancy_usd=0,
            )

        wins   = [t for t in trades if t.outcome == "WIN"]
        losses = [t for t in trades if t.outcome == "LOSS"]

        win_pnls  = [t.pnl_usd for t in wins]
        loss_pnls = [t.pnl_usd for t in losses]

        total_profit = sum(win_pnls) if win_pnls else 0
        total_loss   = abs(sum(loss_pnls)) if loss_pnls else 1

        avg_win  = np.mean(win_pnls)  if win_pnls  else 0
        avg_loss = np.mean(loss_pnls) if loss_pnls else 0

        win_rate      = len(wins) / len(trades)
        profit_factor = total_profit / max(total_loss, 0.01)
        expectancy    = win_rate * avg_win + (1 - win_rate) * avg_loss

        # Max drawdown from equity curve
        eq   = np.array(equity_curve)
        peak = np.maximum.accumulate(eq)
        dd   = (peak - eq) / peak * 100
        max_dd = dd.max()

        # Sharpe (annualised, assuming daily returns approx)
        returns = np.diff(eq) / eq[:-1]
        sharpe  = (np.mean(returns) / np.std(returns) * np.sqrt(252)
                   if len(returns) > 1 and np.std(returns) > 0 else 0)

        avg_rr = np.mean([t.pnl_pips / max(abs(t.entry_price - t.exit_price), 0.01)
                          for t in trades]) if trades else 0

        final = equity_curve[-1]

        return BacktestResult(
            trades=trades,
            final_balance=round(final, 2),
            total_return_pct=round((final - initial_balance) / initial_balance * 100, 2),
            total_trades=len(trades),
            win_count=len(wins),
            loss_count=len(losses),
            win_rate=round(win_rate, 3),
            avg_win_usd=round(avg_win, 2),
            avg_loss_usd=round(avg_loss, 2),
            profit_factor=round(profit_factor, 2),
            max_drawdown_pct=round(max_dd, 2),
            sharpe_ratio=round(sharpe, 2),
            avg_rr_achieved=round(avg_rr, 2),
            expectancy_usd=round(expectancy, 2),
        )

    # ── Walk-Forward Optimization ──────────────────
    def walk_forward(self, df: pd.DataFrame,
                     train_pct: float = 0.7,
                     n_folds:   int   = 5) -> list[BacktestResult]:
        """
        Split data into n_folds. For each fold:
        - Train on train_pct of fold (parameter selection not implemented here,
          but structure is ready for grid search)
        - Test on remaining pct (out-of-sample)
        """
        fold_size  = len(df) // n_folds
        results    = []

        for fold in range(n_folds):
            start = fold * fold_size
            end   = start + fold_size
            fold_df = df.iloc[start:end].reset_index(drop=True)

            split    = int(len(fold_df) * train_pct)
            test_df  = fold_df.iloc[split:].reset_index(drop=True)

            if len(test_df) < 150:
                continue

            result = self.run(test_df, lookback=100)
            results.append(result)
            print(f"  Fold {fold+1}/{n_folds}: "
                  f"WR={result.win_rate*100:.1f}% "
                  f"PF={result.profit_factor:.2f} "
                  f"DD={result.max_drawdown_pct:.1f}% "
                  f"Ret={result.total_return_pct:.1f}%")

        return results

    # ── Monte Carlo Simulation ─────────────────────
    def monte_carlo(self, result: BacktestResult,
                    n_simulations: int = 1000,
                    confidence_levels: list[float] = (0.05, 0.25, 0.50, 0.75, 0.95)
                    ) -> dict:
        """
        Shuffle trade sequence N times to get distribution of outcomes.
        Returns percentile stats for: final balance, max drawdown, Sharpe.
        """
        pnls  = [t.pnl_usd for t in result.trades]
        if not pnls:
            return {}

        sim_finals = []
        sim_dds    = []

        for _ in range(n_simulations):
            shuffled = np.random.permutation(pnls)
            equity   = self.config.initial_balance + np.cumsum(shuffled)
            equity   = np.insert(equity, 0, self.config.initial_balance)
            peak     = np.maximum.accumulate(equity)
            dd       = (peak - equity) / peak * 100
            sim_finals.append(equity[-1])
            sim_dds.append(dd.max())

        sim_finals = np.array(sim_finals)
        sim_dds    = np.array(sim_dds)

        output = {
            "n_simulations": n_simulations,
            "final_balance": {
                f"p{int(p*100)}": round(float(np.percentile(sim_finals, p*100)), 2)
                for p in confidence_levels
            },
            "max_drawdown_pct": {
                f"p{int(p*100)}": round(float(np.percentile(sim_dds, p*100)), 2)
                for p in confidence_levels
            },
            "ruin_probability": round(
                float(np.mean(sim_finals < self.config.initial_balance * 0.5)), 4
            ),
        }

        print(f"\n=== MONTE CARLO ({n_simulations} simulations) ===")
        print(f"  Final Balance P5/P50/P95: "
              f"${output['final_balance']['p5']:,.0f} / "
              f"${output['final_balance']['p50']:,.0f} / "
              f"${output['final_balance']['p95']:,.0f}")
        print(f"  Max DD P50/P95:           "
              f"{output['max_drawdown_pct']['p50']:.1f}% / "
              f"{output['max_drawdown_pct']['p95']:.1f}%")
        print(f"  Ruin probability (<50%):  "
              f"{output['ruin_probability']*100:.1f}%")

        return output


# ──────────────────────────────────────────────
# Demo
# ──────────────────────────────────────────────

if __name__ == "__main__":
    np.random.seed(42)
    n = 2000

    # Simulate XAUUSD-like price with trending + mean-reversion components
    trend  = np.linspace(1800, 2200, n)
    noise  = np.cumsum(np.random.randn(n) * 2.5)
    prices = trend + noise

    df = pd.DataFrame({
        "open":   prices + np.random.randn(n) * 0.3,
        "high":   prices + np.abs(np.random.randn(n)) * 5,
        "low":    prices - np.abs(np.random.randn(n)) * 5,
        "close":  prices,
        "volume": np.abs(np.random.randn(n)) * 1000 + 500,
    })

    config     = BacktestConfig(initial_balance=10_000, risk_pct=1.0)
    backtester = Backtester(config)

    print("\n>>> Running backtest...")
    result = backtester.run(df, lookback=100, verbose=True)
    print(result.summary())

    print("\n>>> Running walk-forward (5 folds)...")
    wf_results = backtester.walk_forward(df, n_folds=5)

    print("\n>>> Running Monte Carlo...")
    mc = backtester.monte_carlo(result, n_simulations=1000)
