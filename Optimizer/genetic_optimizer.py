"""
Chastiefol — Genetic Algorithm Strategy Optimizer
Evolves optimal trading strategy parameters using evolutionary computation.

Optimizable Parameters:
- EMA periods (fast, slow, trend)
- RSI period and thresholds
- ATR multiplier for SL/TP
- Risk:Reward ratio
- Minimum confidence threshold
- Session filter settings
- Confluence minimum score

Fitness Function:
- Primary: Sharpe Ratio (risk-adjusted returns)
- Secondary: Profit Factor, Max Drawdown, Win Rate
- Penalty: Overfitting detection via out-of-sample validation
"""

import logging
import random
import copy
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Callable, Any
from enum import Enum

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("Optimizer.GA")



# ──────────────────────────────────────────────
# Configuration & Data Models
# ──────────────────────────────────────────────

class FitnessMetric(str, Enum):
    SHARPE_RATIO = "sharpe_ratio"
    PROFIT_FACTOR = "profit_factor"
    TOTAL_RETURN = "total_return"
    CALMAR_RATIO = "calmar_ratio"
    SORTINO_RATIO = "sortino_ratio"
    CUSTOM = "custom"


class SelectionMethod(str, Enum):
    TOURNAMENT = "tournament"
    ROULETTE = "roulette"
    RANK = "rank"


class CrossoverMethod(str, Enum):
    SINGLE_POINT = "single_point"
    TWO_POINT = "two_point"
    UNIFORM = "uniform"
    BLEND = "blend"


@dataclass
class ParameterGene:
    """Defines a single optimizable parameter with its range."""
    name: str
    min_value: float
    max_value: float
    step: float = 1.0
    dtype: str = "float"  # "float" or "int"
    description: str = ""

    def random_value(self) -> float:
        """Generate a random value within bounds."""
        if self.dtype == "int":
            steps = int((self.max_value - self.min_value) / self.step)
            return self.min_value + random.randint(0, steps) * self.step
        else:
            value = random.uniform(self.min_value, self.max_value)
            # Round to step
            if self.step > 0:
                value = round(value / self.step) * self.step
            return round(value, 6)

    def mutate(self, current: float, mutation_strength: float = 0.2) -> float:
        """Mutate a value with gaussian noise."""
        range_size = self.max_value - self.min_value
        noise = random.gauss(0, range_size * mutation_strength)
        new_val = current + noise
        # Clamp to bounds
        new_val = max(self.min_value, min(self.max_value, new_val))
        if self.step > 0:
            new_val = round(new_val / self.step) * self.step
        if self.dtype == "int":
            new_val = int(new_val)
        return round(new_val, 6)


@dataclass
class Individual:
    """A single solution in the population (chromosome)."""
    genes: Dict[str, float] = field(default_factory=dict)
    fitness: float = 0.0
    metrics: Dict[str, float] = field(default_factory=dict)
    generation: int = 0
    is_valid: bool = True

    def to_dict(self) -> dict:
        return {
            "genes": self.genes.copy(),
            "fitness": round(self.fitness, 6),
            "metrics": {k: round(v, 4) for k, v in self.metrics.items()},
            "generation": self.generation,
        }


@dataclass
class OptimizationResult:
    """Final optimization output."""
    best_individual: Individual = field(default_factory=Individual)
    best_params: Dict[str, float] = field(default_factory=dict)
    best_fitness: float = 0.0
    best_metrics: Dict[str, float] = field(default_factory=dict)
    generations_run: int = 0
    total_evaluations: int = 0
    population_history: List[Dict] = field(default_factory=list)
    convergence_curve: List[float] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    validation_fitness: float = 0.0  # Out-of-sample performance

    def to_dict(self) -> dict:
        return {
            "best_params": {k: round(v, 4) for k, v in self.best_params.items()},
            "best_fitness": round(self.best_fitness, 6),
            "best_metrics": {k: round(v, 4) for k, v in self.best_metrics.items()},
            "generations": self.generations_run,
            "evaluations": self.total_evaluations,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "validation_fitness": round(self.validation_fitness, 4),
            "convergence": [round(x, 4) for x in self.convergence_curve[-20:]],
        }


@dataclass
class OptimizerConfig:
    """Genetic algorithm configuration."""
    # Population
    population_size: int = 50
    generations: int = 100
    elite_count: int = 5  # Best individuals kept unchanged

    # Genetic operators
    crossover_rate: float = 0.8
    mutation_rate: float = 0.15
    mutation_strength: float = 0.2  # Gaussian noise scale
    selection_method: SelectionMethod = SelectionMethod.TOURNAMENT
    crossover_method: CrossoverMethod = CrossoverMethod.BLEND
    tournament_size: int = 3

    # Fitness
    fitness_metric: FitnessMetric = FitnessMetric.SHARPE_RATIO
    min_trades: int = 20  # Minimum trades for valid fitness

    # Validation
    train_split: float = 0.7  # 70% train, 30% validation
    penalize_overfitting: bool = True
    overfit_penalty: float = 0.3  # Penalty weight for train/val gap

    # Convergence
    early_stop_generations: int = 20  # Stop if no improvement for N gens
    target_fitness: float = 3.0  # Stop if fitness exceeds this

    # Diversity
    diversity_threshold: float = 0.1  # Min genetic diversity to maintain
    immigration_rate: float = 0.05  # Random new individuals per generation



# ──────────────────────────────────────────────
# Default Parameter Space for XAUUSD
# ──────────────────────────────────────────────

DEFAULT_PARAM_SPACE = [
    ParameterGene("ema_fast", 10, 30, step=2, dtype="int", description="Fast EMA period"),
    ParameterGene("ema_slow", 40, 80, step=5, dtype="int", description="Slow EMA period"),
    ParameterGene("ema_trend", 150, 250, step=10, dtype="int", description="Trend EMA period"),
    ParameterGene("rsi_period", 7, 21, step=1, dtype="int", description="RSI period"),
    ParameterGene("rsi_overbought", 65, 80, step=1, dtype="int", description="RSI overbought"),
    ParameterGene("rsi_oversold", 20, 35, step=1, dtype="int", description="RSI oversold"),
    ParameterGene("atr_period", 10, 20, step=1, dtype="int", description="ATR period"),
    ParameterGene("sl_atr_mult", 1.0, 3.0, step=0.1, description="SL ATR multiplier"),
    ParameterGene("tp_rr_ratio", 1.5, 4.0, step=0.1, description="Take profit R:R"),
    ParameterGene("min_confidence", 0.40, 0.75, step=0.05, description="Min confidence threshold"),
    ParameterGene("min_confluence", 2, 6, step=1, dtype="int", description="Min confluence score"),
    ParameterGene("cooldown_bars", 2, 10, step=1, dtype="int", description="Bars between signals"),
    ParameterGene("risk_pct", 0.5, 2.5, step=0.1, description="Risk % per trade"),
]


# ──────────────────────────────────────────────
# Genetic Optimizer
# ──────────────────────────────────────────────

class GeneticOptimizer:
    """
    Genetic algorithm for optimizing trading strategy parameters.

    Usage:
        config = OptimizerConfig(population_size=50, generations=100)
        optimizer = GeneticOptimizer(config, param_space=DEFAULT_PARAM_SPACE)

        # Set fitness function (runs backtest with given params, returns metrics)
        optimizer.set_fitness_function(my_backtest_function)

        # Run optimization
        result = optimizer.optimize(train_data, validation_data)

        print(result.best_params)
        print(result.best_fitness)
    """

    def __init__(self, config: OptimizerConfig = None,
                 param_space: List[ParameterGene] = None):
        self.config = config or OptimizerConfig()
        self.param_space = param_space or DEFAULT_PARAM_SPACE
        self._fitness_fn: Optional[Callable] = None
        self._population: List[Individual] = []
        self._best_ever: Optional[Individual] = None
        self._generation = 0
        self._no_improve_count = 0
        self._total_evals = 0

        log.info(f"Genetic Optimizer initialized | "
                 f"Pop: {self.config.population_size} | "
                 f"Gens: {self.config.generations} | "
                 f"Params: {len(self.param_space)} | "
                 f"Fitness: {self.config.fitness_metric.value}")

    def set_fitness_function(self, fn: Callable):
        """
        Set the fitness evaluation function.
        fn(params: dict, data: pd.DataFrame) -> dict
        Must return dict with at least 'sharpe_ratio', 'total_pnl', 'max_drawdown',
        'win_rate', 'profit_factor', 'total_trades'.
        """
        self._fitness_fn = fn

    def optimize(self, train_data: pd.DataFrame,
                 validation_data: Optional[pd.DataFrame] = None) -> OptimizationResult:
        """
        Run the genetic algorithm optimization.

        Args:
            train_data: OHLCV DataFrame for in-sample optimization
            validation_data: OHLCV DataFrame for out-of-sample validation

        Returns:
            OptimizationResult with best parameters and metrics
        """
        import time
        start_time = time.time()

        if not self._fitness_fn:
            raise ValueError("Fitness function not set. Call set_fitness_function() first.")

        # Split data if no validation provided
        if validation_data is None and self.config.train_split < 1.0:
            split_idx = int(len(train_data) * self.config.train_split)
            validation_data = train_data.iloc[split_idx:].reset_index(drop=True)
            train_data = train_data.iloc[:split_idx].reset_index(drop=True)

        result = OptimizationResult()

        # Initialize population
        self._initialize_population()
        log.info(f"Population initialized: {len(self._population)} individuals")

        # Evolution loop
        for gen in range(self.config.generations):
            self._generation = gen

            # Evaluate fitness
            self._evaluate_population(train_data)

            # Sort by fitness (descending)
            self._population.sort(key=lambda ind: ind.fitness, reverse=True)

            # Track best
            current_best = self._population[0]
            if self._best_ever is None or current_best.fitness > self._best_ever.fitness:
                self._best_ever = copy.deepcopy(current_best)
                self._no_improve_count = 0
            else:
                self._no_improve_count += 1

            # Record convergence
            result.convergence_curve.append(current_best.fitness)

            # Log progress
            avg_fitness = np.mean([ind.fitness for ind in self._population])
            if gen % 10 == 0 or gen == self.config.generations - 1:
                log.info(f"Gen {gen:3d} | Best: {current_best.fitness:.4f} | "
                         f"Avg: {avg_fitness:.4f} | "
                         f"Best-ever: {self._best_ever.fitness:.4f}")

            # Early stopping
            if self._no_improve_count >= self.config.early_stop_generations:
                log.info(f"Early stop: no improvement for {self._no_improve_count} generations")
                break
            if current_best.fitness >= self.config.target_fitness:
                log.info(f"Target fitness {self.config.target_fitness} reached!")
                break

            # Create next generation
            self._evolve()

        # Validation
        validation_fitness = 0.0
        if validation_data is not None and self._best_ever:
            val_metrics = self._fitness_fn(self._best_ever.genes, validation_data)
            validation_fitness = self._extract_fitness(val_metrics)
            log.info(f"Validation fitness: {validation_fitness:.4f} "
                     f"(train: {self._best_ever.fitness:.4f})")

        # Build result
        elapsed = time.time() - start_time
        result.best_individual = self._best_ever
        result.best_params = self._best_ever.genes.copy()
        result.best_fitness = self._best_ever.fitness
        result.best_metrics = self._best_ever.metrics.copy()
        result.generations_run = self._generation + 1
        result.total_evaluations = self._total_evals
        result.elapsed_seconds = elapsed
        result.validation_fitness = validation_fitness

        log.info(f"\n{'='*50}")
        log.info(f"  OPTIMIZATION COMPLETE")
        log.info(f"  Generations: {result.generations_run}")
        log.info(f"  Evaluations: {result.total_evaluations}")
        log.info(f"  Time: {elapsed:.1f}s")
        log.info(f"  Best fitness: {result.best_fitness:.4f}")
        log.info(f"  Validation: {validation_fitness:.4f}")
        log.info(f"  Best params: {result.best_params}")
        log.info(f"{'='*50}\n")

        return result

    # ──────────────────────────────────────────
    # Population Management
    # ──────────────────────────────────────────

    def _initialize_population(self):
        """Create initial random population."""
        self._population = []
        for _ in range(self.config.population_size):
            ind = Individual(generation=0)
            for gene in self.param_space:
                ind.genes[gene.name] = gene.random_value()
            self._population.append(ind)

    def _evaluate_population(self, data: pd.DataFrame):
        """Evaluate fitness for all individuals."""
        for ind in self._population:
            if ind.fitness == 0.0:  # Only evaluate unevaluated
                metrics = self._fitness_fn(ind.genes, data)
                ind.metrics = metrics
                ind.fitness = self._extract_fitness(metrics)
                ind.is_valid = metrics.get("total_trades", 0) >= self.config.min_trades
                if not ind.is_valid:
                    ind.fitness = -999  # Penalize insufficient trades
                self._total_evals += 1

    def _extract_fitness(self, metrics: dict) -> float:
        """Extract fitness value from metrics based on configured metric."""
        metric_map = {
            FitnessMetric.SHARPE_RATIO: "sharpe_ratio",
            FitnessMetric.PROFIT_FACTOR: "profit_factor",
            FitnessMetric.TOTAL_RETURN: "total_pnl",
            FitnessMetric.CALMAR_RATIO: "calmar_ratio",
            FitnessMetric.SORTINO_RATIO: "sortino_ratio",
        }
        key = metric_map.get(self.config.fitness_metric, "sharpe_ratio")
        fitness = metrics.get(key, 0.0)

        # Penalize extreme drawdown
        max_dd = metrics.get("max_drawdown", 0)
        if max_dd > 20:
            fitness *= 0.5
        elif max_dd > 15:
            fitness *= 0.7

        return fitness

    # ──────────────────────────────────────────
    # Genetic Operators
    # ──────────────────────────────────────────

    def _evolve(self):
        """Create next generation using selection, crossover, mutation."""
        new_population = []

        # Elitism: keep top N individuals unchanged
        elites = self._population[:self.config.elite_count]
        for elite in elites:
            elite_copy = copy.deepcopy(elite)
            elite_copy.generation = self._generation + 1
            new_population.append(elite_copy)

        # Immigration: add random new individuals for diversity
        immigration_count = max(1, int(self.config.population_size * self.config.immigration_rate))
        for _ in range(immigration_count):
            immigrant = Individual(generation=self._generation + 1)
            for gene in self.param_space:
                immigrant.genes[gene.name] = gene.random_value()
            new_population.append(immigrant)

        # Fill remaining with crossover + mutation
        while len(new_population) < self.config.population_size:
            # Selection
            parent_a = self._select()
            parent_b = self._select()

            # Crossover
            if random.random() < self.config.crossover_rate:
                child = self._crossover(parent_a, parent_b)
            else:
                child = copy.deepcopy(parent_a)

            # Mutation
            child = self._mutate(child)
            child.generation = self._generation + 1
            child.fitness = 0.0  # Reset for re-evaluation
            new_population.append(child)

        self._population = new_population[:self.config.population_size]

    def _select(self) -> Individual:
        """Select a parent based on configured method."""
        if self.config.selection_method == SelectionMethod.TOURNAMENT:
            return self._tournament_select()
        elif self.config.selection_method == SelectionMethod.ROULETTE:
            return self._roulette_select()
        else:
            return self._rank_select()

    def _tournament_select(self) -> Individual:
        """Tournament selection."""
        candidates = random.sample(self._population,
                                   min(self.config.tournament_size, len(self._population)))
        return max(candidates, key=lambda ind: ind.fitness)

    def _roulette_select(self) -> Individual:
        """Fitness-proportional roulette selection."""
        # Shift fitness to positive
        min_fit = min(ind.fitness for ind in self._population)
        shifted = [ind.fitness - min_fit + 0.01 for ind in self._population]
        total = sum(shifted)
        pick = random.uniform(0, total)
        current = 0
        for i, fit in enumerate(shifted):
            current += fit
            if current >= pick:
                return self._population[i]
        return self._population[-1]

    def _rank_select(self) -> Individual:
        """Rank-based selection."""
        n = len(self._population)
        ranks = list(range(n, 0, -1))  # Best has highest rank
        total = sum(ranks)
        pick = random.uniform(0, total)
        current = 0
        for i, rank in enumerate(ranks):
            current += rank
            if current >= pick:
                return self._population[i]
        return self._population[0]

    def _crossover(self, parent_a: Individual, parent_b: Individual) -> Individual:
        """Perform crossover between two parents."""
        child = Individual()

        if self.config.crossover_method == CrossoverMethod.UNIFORM:
            for gene in self.param_space:
                if random.random() < 0.5:
                    child.genes[gene.name] = parent_a.genes[gene.name]
                else:
                    child.genes[gene.name] = parent_b.genes[gene.name]

        elif self.config.crossover_method == CrossoverMethod.BLEND:
            # BLX-alpha crossover (blend between parents)
            alpha = 0.3
            for gene in self.param_space:
                val_a = parent_a.genes[gene.name]
                val_b = parent_b.genes[gene.name]
                low = min(val_a, val_b)
                high = max(val_a, val_b)
                spread = high - low
                new_val = random.uniform(low - alpha * spread, high + alpha * spread)
                new_val = max(gene.min_value, min(gene.max_value, new_val))
                if gene.step > 0:
                    new_val = round(new_val / gene.step) * gene.step
                if gene.dtype == "int":
                    new_val = int(new_val)
                child.genes[gene.name] = round(new_val, 6)

        elif self.config.crossover_method == CrossoverMethod.SINGLE_POINT:
            point = random.randint(1, len(self.param_space) - 1)
            for i, gene in enumerate(self.param_space):
                if i < point:
                    child.genes[gene.name] = parent_a.genes[gene.name]
                else:
                    child.genes[gene.name] = parent_b.genes[gene.name]

        elif self.config.crossover_method == CrossoverMethod.TWO_POINT:
            n = len(self.param_space)
            p1, p2 = sorted(random.sample(range(n), 2))
            for i, gene in enumerate(self.param_space):
                if p1 <= i < p2:
                    child.genes[gene.name] = parent_b.genes[gene.name]
                else:
                    child.genes[gene.name] = parent_a.genes[gene.name]

        return child

    def _mutate(self, individual: Individual) -> Individual:
        """Apply mutation to an individual's genes."""
        gene_map = {g.name: g for g in self.param_space}
        for gene_name in individual.genes:
            if random.random() < self.config.mutation_rate:
                gene_def = gene_map.get(gene_name)
                if gene_def:
                    individual.genes[gene_name] = gene_def.mutate(
                        individual.genes[gene_name],
                        self.config.mutation_strength,
                    )
        return individual

    # ──────────────────────────────────────────
    # Utilities
    # ──────────────────────────────────────────

    def get_population_diversity(self) -> float:
        """Calculate genetic diversity of current population (0-1)."""
        if len(self._population) < 2:
            return 1.0
        gene_names = [g.name for g in self.param_space]
        diversities = []
        for name in gene_names:
            values = [ind.genes.get(name, 0) for ind in self._population]
            gene_def = next((g for g in self.param_space if g.name == name), None)
            if gene_def:
                gene_range = gene_def.max_value - gene_def.min_value
                if gene_range > 0:
                    std = np.std(values)
                    diversities.append(std / gene_range)
        return float(np.mean(diversities)) if diversities else 0.0

    def get_top_n(self, n: int = 5) -> List[dict]:
        """Get top N individuals as dictionaries."""
        sorted_pop = sorted(self._population, key=lambda x: x.fitness, reverse=True)
        return [ind.to_dict() for ind in sorted_pop[:n]]



# ──────────────────────────────────────────────
# Built-in Fitness Function (Simple Backtest)
# ──────────────────────────────────────────────

def simple_backtest_fitness(params: dict, data: pd.DataFrame) -> dict:
    """
    Simple backtester that evaluates a parameter set.
    Uses EMA crossover + RSI filter as the strategy.
    Returns metrics dict for fitness evaluation.

    This is a reference implementation — replace with your full backtester
    for production use.
    """
    if len(data) < 100:
        return {"sharpe_ratio": 0, "total_pnl": 0, "max_drawdown": 0,
                "win_rate": 0, "profit_factor": 0, "total_trades": 0}

    close = data["close"].values
    high = data["high"].values
    low = data["low"].values

    # Extract params
    ema_fast_p = int(params.get("ema_fast", 20))
    ema_slow_p = int(params.get("ema_slow", 50))
    rsi_period = int(params.get("rsi_period", 14))
    rsi_ob = params.get("rsi_overbought", 70)
    rsi_os = params.get("rsi_oversold", 30)
    sl_mult = params.get("sl_atr_mult", 1.5)
    tp_rr = params.get("tp_rr_ratio", 2.0)
    risk_pct = params.get("risk_pct", 1.0)

    # Calculate indicators
    ema_fast = pd.Series(close).ewm(span=ema_fast_p, adjust=False).mean().values
    ema_slow = pd.Series(close).ewm(span=ema_slow_p, adjust=False).mean().values

    # RSI
    delta = pd.Series(close).diff().values
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(rsi_period).mean().values
    avg_loss = pd.Series(loss).rolling(rsi_period).mean().values
    rs = np.where(avg_loss != 0, avg_gain / avg_loss, 0)
    rsi = 100 - (100 / (1 + rs))

    # ATR
    tr = np.maximum(high - low,
                    np.maximum(np.abs(high - np.roll(close, 1)),
                               np.abs(low - np.roll(close, 1))))
    atr = pd.Series(tr).ewm(span=14, adjust=False).mean().values

    # Simulate trades
    trades = []
    balance = 10000.0
    in_trade = False
    entry_price = 0.0
    sl_price = 0.0
    tp_price = 0.0
    direction = ""

    lookback = max(ema_slow_p, rsi_period) + 5

    for i in range(lookback, len(close)):
        if not in_trade:
            # BUY signal: fast > slow + RSI not overbought
            if (ema_fast[i] > ema_slow[i] and ema_fast[i-1] <= ema_slow[i-1]
                    and rsi[i] < rsi_ob and rsi[i] > rsi_os):
                entry_price = close[i]
                sl_dist = atr[i] * sl_mult
                sl_price = entry_price - sl_dist
                tp_price = entry_price + sl_dist * tp_rr
                direction = "BUY"
                in_trade = True

            # SELL signal
            elif (ema_fast[i] < ema_slow[i] and ema_fast[i-1] >= ema_slow[i-1]
                    and rsi[i] > rsi_os and rsi[i] < rsi_ob):
                entry_price = close[i]
                sl_dist = atr[i] * sl_mult
                sl_price = entry_price + sl_dist
                tp_price = entry_price - sl_dist * tp_rr
                direction = "SELL"
                in_trade = True

        else:
            # Check SL/TP
            if direction == "BUY":
                if low[i] <= sl_price:
                    pnl = sl_price - entry_price
                    trades.append(pnl)
                    balance += pnl * (balance * risk_pct / 100) / max(abs(entry_price - sl_price), 0.01)
                    in_trade = False
                elif high[i] >= tp_price:
                    pnl = tp_price - entry_price
                    trades.append(pnl)
                    balance += pnl * (balance * risk_pct / 100) / max(abs(entry_price - sl_price), 0.01)
                    in_trade = False
            else:
                if high[i] >= sl_price:
                    pnl = entry_price - sl_price
                    trades.append(pnl)
                    balance += pnl * (balance * risk_pct / 100) / max(abs(entry_price - sl_price), 0.01)
                    in_trade = False
                elif low[i] <= tp_price:
                    pnl = entry_price - tp_price
                    trades.append(pnl)
                    balance += pnl * (balance * risk_pct / 100) / max(abs(entry_price - sl_price), 0.01)
                    in_trade = False

    # Calculate metrics
    if not trades:
        return {"sharpe_ratio": 0, "total_pnl": 0, "max_drawdown": 0,
                "win_rate": 0, "profit_factor": 0, "total_trades": 0}

    trades_arr = np.array(trades)
    wins = trades_arr[trades_arr > 0]
    losses = trades_arr[trades_arr < 0]

    total_pnl = balance - 10000.0
    win_rate = len(wins) / len(trades_arr) * 100 if len(trades_arr) > 0 else 0
    gross_profit = wins.sum() if len(wins) > 0 else 0
    gross_loss = abs(losses.sum()) if len(losses) > 0 else 0.01
    profit_factor = gross_profit / gross_loss

    # Sharpe ratio (simplified)
    returns = trades_arr / max(abs(trades_arr).mean(), 0.01)
    sharpe = (returns.mean() / max(returns.std(), 0.001)) * np.sqrt(252) if len(returns) > 1 else 0

    # Max drawdown
    equity_curve = 10000.0 + np.cumsum(trades_arr * 10)  # Simplified
    peak = np.maximum.accumulate(equity_curve)
    drawdown = (peak - equity_curve) / peak * 100
    max_dd = drawdown.max() if len(drawdown) > 0 else 0

    return {
        "sharpe_ratio": float(sharpe),
        "total_pnl": float(total_pnl),
        "max_drawdown": float(max_dd),
        "win_rate": float(win_rate),
        "profit_factor": float(profit_factor),
        "total_trades": len(trades_arr),
        "avg_trade": float(trades_arr.mean()),
        "calmar_ratio": float(total_pnl / max(max_dd, 0.01)),
    }


# ──────────────────────────────────────────────
# Standalone Test
# ──────────────────────────────────────────────

if __name__ == "__main__":
    # Generate synthetic XAUUSD data
    np.random.seed(42)
    n = 1000
    prices = 2300 + np.cumsum(np.random.randn(n) * 2.0)
    test_data = pd.DataFrame({
        "open": prices + np.random.randn(n) * 0.3,
        "high": prices + np.abs(np.random.randn(n)) * 5,
        "low": prices - np.abs(np.random.randn(n)) * 5,
        "close": prices,
        "volume": np.abs(np.random.randn(n)) * 1000 + 500,
    })

    # Configure optimizer
    config = OptimizerConfig(
        population_size=30,
        generations=20,
        elite_count=3,
        early_stop_generations=10,
    )

    optimizer = GeneticOptimizer(config)
    optimizer.set_fitness_function(simple_backtest_fitness)

    # Run
    result = optimizer.optimize(test_data)

    print(f"\n{'='*50}")
    print(f"  OPTIMIZATION RESULT")
    print(f"{'='*50}")
    print(f"  Best Fitness: {result.best_fitness:.4f}")
    print(f"  Generations: {result.generations_run}")
    print(f"  Evaluations: {result.total_evaluations}")
    print(f"  Time: {result.elapsed_seconds:.1f}s")
    print(f"\n  Best Parameters:")
    for k, v in result.best_params.items():
        print(f"    {k:20s}: {v}")
    print(f"\n  Best Metrics:")
    for k, v in result.best_metrics.items():
        print(f"    {k:20s}: {v:.4f}")
    print(f"{'='*50}\n")
