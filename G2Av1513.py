from dataclasses import dataclass
from math import exp, isfinite, log, sqrt, pi
from typing import List, Optional, Tuple


@dataclass
class RatingG2A:
    mu: float = 1500.0
    rd: float = 200.0
    volatility: float = 0.06


class Glicko2G2A:
    def __init__(
        self,
        mu: float = 1500.0,
        rd: float = 200.0,
        tau: float = 0.8,
        epsilon: float = 1e-6,
        g_constant: Optional[float] = None,
        draw_adjustment: float = 0.0,
        n_features: int = 0,
    ) -> None:
        self.mu = mu
        self.rd = rd
        self.tau = tau
        self.q = log(10.0) / 400.0
        self.q1 = 400.0 / log(10.0)
        self.g_constant = (3.0 / (pi ** 2)) if g_constant is None else g_constant
        self.epsilon = max(epsilon, 1e-12)
        self.draw_adjustment = float(draw_adjustment)
        self.beta = [0.0] * (max(0, n_features) + 1)
        # TODO(beta): Replace with learned coefficients once optimization is done.
        self.DUMMY_BETA = [0.0] * (max(0, n_features) + 1)

    def set_draw_adjustment(self, draw_adjustment: float) -> None:
        self.draw_adjustment = float(draw_adjustment)

    def set_beta(self, beta_list: List[float]) -> None:
        if len(beta_list) != len(self.beta):
            raise ValueError(
                f"Se esperaban {len(self.beta)} coeficientes, se recibieron {len(beta_list)}"
            )
        self.beta = beta_list[:]
        # Template para agregar betas (reemplaza con tus valores):
        # beta = [BETA_0, BETA_1, BETA_2, ...]
        # model.set_beta(beta)

    def set_dummy_beta(self) -> None:
        """Set placeholder coefficients until real beta values are available."""
        self.beta = self.DUMMY_BETA[:]

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        return min(max(value, lo), hi)

    def _safe_float(self, value: float, default: float = 0.0) -> float:
        return value if isfinite(value) else default

    def _compute_advantage(self, features: Optional[List[float]]) -> float:
        if features is None:
            return 0.0
        advantage = self.beta[0]
        for i, f in enumerate(features):
            if i + 1 >= len(self.beta):
                break
            advantage += self.beta[i + 1] * self._safe_float(float(f), 0.0)
        return float(advantage)

    def _pandemic_multiplier(self, pandemic: Optional[float], attenuation: float) -> float:
        flag = self._safe_float(float(pandemic) if pandemic is not None else 0.0, 0.0)
        if flag >= 0.5:
            return self._clamp(float(attenuation), 0.0, 1.0)
        return 1.0

    def _draw_logit_from_prob(self, draw_prob: float) -> float:
        # Main path: draw_prob is a raw probability in (0, 1).
        p = self._clamp(self._safe_float(draw_prob, 0.0), 1e-12, 1.0 - 1e-12)
        return float(log(p / (1.0 - p)))

    # Alternative: if you want to pass logit(draw_prob) directly, use this instead.
    # def _draw_logit_from_prob(self, draw_prob: float) -> float:
    #     return float(self._clamp(self._safe_float(draw_prob, 0.0), -20.0, 20.0))

    def _expected_score(self, mu: float, mu_opp: float, g: float, skellam_draw_prob: float) -> float:
        delta_mu = g * (mu - mu_opp)
        delta_mu = self._clamp(delta_mu, -50.0, 50.0)
        s_logit = self._draw_logit_from_prob(skellam_draw_prob)
        draw_term = exp(self.draw_adjustment + s_logit)
        exp_delta = exp(delta_mu)
        denom = 1.0 + exp_delta + draw_term
        win_prob = exp_delta / denom
        tie_prob = draw_term / denom
        expected = win_prob + 0.5 * tie_prob
        return self._clamp(expected, 1e-12, 1.0 - 1e-12)

    def _convert_into_glicko2(self, rating: RatingG2A) -> RatingG2A:
        mu = (rating.mu - self.mu) * self.q
        phi = rating.rd * self.q
        return RatingG2A(mu, phi, rating.volatility)

    def _convert_from_glicko2(self, rating: RatingG2A) -> RatingG2A:
        mu = rating.mu * self.q1 + self.mu
        phi = rating.rd * self.q1
        return RatingG2A(mu, phi, rating.volatility)

    def _g(self, phi: float, phi_opp: float) -> float:
        return 1.0 / sqrt(1.0 + self.g_constant * (phi * phi + phi_opp * phi_opp))

    @staticmethod
    def _v(g: float, expected_score: float) -> float:
        denom = g * g * expected_score * (1.0 - expected_score)
        return 1.0 / denom if denom != 0.0 else 1e12

    @staticmethod
    def _rating_improvement(v: float, g: float, outcome: float, expected_score: float) -> float:
        return v * g * (outcome - expected_score)

    @staticmethod
    def _f(delta_sq: float, v: float, phi_sq: float, vol_sq: float, x: float) -> float:
        exp_x = exp(x)
        temp = phi_sq + v + exp_x
        first = exp_x * (delta_sq - temp) / (2.0 * temp * temp)
        second = x - log(vol_sq)
        return first - second

    def _update_volatility(
        self,
        rating: RatingG2A,
        rating_opp: RatingG2A,
        outcome: float,
        skellam_draw_probability: float,
    ) -> float:
        mu, phi = rating.mu, rating.rd
        phi_sq = phi * phi
        vol_sq = rating.volatility * rating.volatility
        mu_opp, phi_opp = rating_opp.mu, rating_opp.rd

        g = self._g(phi, phi_opp)
        expected = self._expected_score(mu, mu_opp, g, skellam_draw_probability)
        v = self._v(g, expected)
        delta = self._rating_improvement(v, g, outcome, expected)
        delta_sq = delta * delta

        a = log(vol_sq)
        temp = delta_sq - phi_sq - v
        if temp > 0.0:
            b = log(temp)
        else:
            k = 1
            while self._f(delta_sq, v, phi_sq, vol_sq, a - k * self.tau) < 0.0:
                k += 1
            b = a - k * self.tau

        f_a = self._f(delta_sq, v, phi_sq, vol_sq, a)
        f_b = self._f(delta_sq, v, phi_sq, vol_sq, b)
        while abs(b - a) > self.epsilon:
            c = a + (a - b) * f_a / (f_b - f_a)
            f_c = self._f(delta_sq, v, phi_sq, vol_sq, c)
            if f_c * f_b < 0.0:
                a, f_a = b, f_b
            else:
                f_a /= 2.0
            b, f_b = c, f_c
        return exp(a / 2.0)

    def _update_rating(
        self,
        rating: RatingG2A,
        rating_opp: RatingG2A,
        outcome: float,
        skellam_draw_probability: float,
    ) -> RatingG2A:
        rating = self._convert_into_glicko2(rating)
        rating_opp = self._convert_into_glicko2(rating_opp)
        mu, phi = rating.mu, rating.rd
        mu_opp, phi_opp = rating_opp.mu, rating_opp.rd

        g = self._g(phi, phi_opp)
        expected = self._expected_score(mu, mu_opp, g, skellam_draw_probability)
        v = self._v(g, expected)
        new_vol = self._update_volatility(rating, rating_opp, outcome, skellam_draw_probability)

        phi_star_sq = phi * phi + new_vol * new_vol
        new_phi = 1.0 / sqrt(1.0 / phi_star_sq + 1.0 / v)
        new_mu = mu + new_phi * new_phi * g * (outcome - expected)

        new_rating = RatingG2A(new_mu, new_phi, new_vol)
        return self._convert_from_glicko2(new_rating)

    @staticmethod
    def _increase_mu(rating: RatingG2A, addition: float) -> RatingG2A:
        return RatingG2A(rating.mu + addition, rating.rd, rating.volatility)

    def rate(
        self,
        home_rating: RatingG2A,
        away_rating: RatingG2A,
        outcome: str,
        features: Optional[List[float]] = None,
        advantage: Optional[float] = None,
        home_advantage: float = 0.0,
        home_pandemic_advantage: Optional[float] = None,
        use_pandemic: bool = False,
        pandemic: Optional[float] = 0.0,
        pandemic_attenuation: float = 1.0,
        skellam_draw_probability: float = 0.0,
    ) -> Tuple[RatingG2A, RatingG2A]:
        if outcome not in {"H", "D", "A"}:
            raise ValueError("outcome must be 'H', 'D', or 'A'")

        # Main path: advantage only from external features.
        adv = self._compute_advantage(features)
        adv *= self._pandemic_multiplier(pandemic, pandemic_attenuation)

        # Alternative: if you want to add league/home advantage on top of features, use this.
        # match_adv = home_pandemic_advantage if use_pandemic and home_pandemic_advantage is not None else home_advantage
        # adv = self._compute_advantage(features) + float(match_adv)

        # Alternative: if you want to allow a manual advantage override, use this.
        # if advantage is not None:
        #     adv = float(advantage)

        home_adv = self._increase_mu(home_rating, adv)
        away_adv = self._increase_mu(away_rating, -adv)

        if outcome == "H":
            new_home = self._update_rating(home_adv, away_adv, 1.0, skellam_draw_probability)
            new_away = self._update_rating(away_adv, home_adv, 0.0, skellam_draw_probability)
        elif outcome == "D":
            new_home = self._update_rating(home_adv, away_adv, 0.5, skellam_draw_probability)
            new_away = self._update_rating(away_adv, home_adv, 0.5, skellam_draw_probability)
        else:
            new_home = self._update_rating(home_adv, away_adv, 0.0, skellam_draw_probability)
            new_away = self._update_rating(away_adv, home_adv, 1.0, skellam_draw_probability)

        return self._increase_mu(new_home, -adv), self._increase_mu(new_away, adv)

    def probabilities(
        self,
        team: RatingG2A,
        opp_team: RatingG2A,
        features: Optional[List[float]] = None,
        advantage: Optional[float] = None,
        home_advantage: float = 0.0,
        home_pandemic_advantage: Optional[float] = None,
        use_pandemic: bool = False,
        pandemic: Optional[float] = 0.0,
        pandemic_attenuation: float = 1.0,
        skellam_draw_probability: float = 0.0,
    ) -> Tuple[float, float, float]:
        # Main path: advantage only from external features.
        adv = self._compute_advantage(features)
        adv *= self._pandemic_multiplier(pandemic, pandemic_attenuation)

        # Alternative: if you want to add league/home advantage on top of features, use this.
        # match_adv = home_pandemic_advantage if use_pandemic and home_pandemic_advantage is not None else home_advantage
        # adv = self._compute_advantage(features) + float(match_adv)

        # Alternative: if you want to allow a manual advantage override, use this.
        # if advantage is not None:
        #     adv = float(advantage)

        mu = (team.mu + adv - self.mu) * self.q
        mu_opp = (opp_team.mu - adv - self.mu) * self.q
        phi = team.rd * self.q
        phi_opp = opp_team.rd * self.q

        g = 1.0 / sqrt(1.0 + self.g_constant * (phi * phi + phi_opp * phi_opp))
        delta_mu = self._clamp(g * (mu - mu_opp), -50.0, 50.0)

        s_logit = self._draw_logit_from_prob(skellam_draw_probability)
        draw_term = exp(self.draw_adjustment + s_logit)
        exp_delta = exp(delta_mu)
        denom = 1.0 + exp_delta + draw_term
        win = exp_delta / denom
        loss = 1.0 / denom
        tie = draw_term / denom

        win = self._clamp(win, 0.0, 1.0)
        loss = self._clamp(loss, 0.0, 1.0)
        tie = self._clamp(tie, 0.0, 1.0)

        return win, tie, loss
