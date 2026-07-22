from dataclasses import dataclass
from math import exp, isfinite, log, sqrt, pi
from typing import Optional, Tuple


@dataclass
class Rating:
	mu: float = 1500.0
	rd: float = 200.0
	volatility: float = 0.06


class Glicko2:
	def __init__(
		self,
		mu: float = 1500.0,
		rd: float = 200.0,
		tau: float = 0.8,
		epsilon: float = 1e-6,
		g_constant: Optional[float] = None,
		draw_adjustment: float = 0.0,
	) -> None:
		self.mu = mu
		self.rd = rd
		self.tau = tau
		self.q = log(10.0) / 400.0
		self.q1 = 400.0 / log(10.0)
		self.g_constant = (3.0 / (pi ** 2)) if g_constant is None else g_constant
		self.epsilon = max(epsilon, 1e-12)
		self.draw_adjustment = float(draw_adjustment)

	def set_draw_adjustment(self, draw_adjustment: float) -> None:
		self.draw_adjustment = float(draw_adjustment)

	@staticmethod
	def _clamp(value: float, lo: float, hi: float) -> float:
		return min(max(value, lo), hi)

	def _safe_float(self, value: float, default: float = 0.0) -> float:
		return value if isfinite(value) else default

	def _match_advantage(
		self,
		home_advantage: float,
		home_pandemic_advantage: Optional[float],
		use_pandemic: bool,
	) -> float:
		if use_pandemic and home_pandemic_advantage is not None:
			return float(home_pandemic_advantage)
		return float(home_advantage)

	def _expected_score(self, mu: float, mu_opp: float, g: float, skellam_draw_prob: float) -> float:
		delta_mu = g * (mu - mu_opp)
		delta_mu = self._clamp(delta_mu, -50.0, 50.0)
		s_prob = self._clamp(self._safe_float(skellam_draw_prob, 0.0), 0.0, 1.0)
		exp_delta = exp(delta_mu)
		draw_term = exp(self.draw_adjustment + s_prob)
		denom = 1.0 + exp_delta + draw_term
		win_prob = exp_delta / denom
		tie_prob = draw_term / denom
		expected = win_prob + 0.5 * tie_prob
		return self._clamp(expected, 1e-12, 1.0 - 1e-12)

	def _convert_into_glicko2(self, rating: Rating) -> Rating:
		mu = (rating.mu - self.mu) * self.q
		phi = rating.rd * self.q
		return Rating(mu, phi, rating.volatility)

	def _convert_from_glicko2(self, rating: Rating) -> Rating:
		mu = rating.mu * self.q1 + self.mu
		phi = rating.rd * self.q1
		return Rating(mu, phi, rating.volatility)

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
		rating: Rating,
		rating_opp: Rating,
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
		rating: Rating,
		rating_opp: Rating,
		outcome: float,
		skellam_draw_probability: float,
	) -> Rating:
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

		new_rating = Rating(new_mu, new_phi, new_vol)
		return self._convert_from_glicko2(new_rating)

	@staticmethod
	def _increase_mu(rating: Rating, addition: float) -> Rating:
		return Rating(rating.mu + addition, rating.rd, rating.volatility)

	def rate(
		self,
		home_rating: Rating,
		away_rating: Rating,
		outcome: str,
		features: Optional[list] = None,
		advantage: Optional[float] = None,
		home_advantage: float = 0.0,
		home_pandemic_advantage: Optional[float] = None,
		use_pandemic: bool = False,
		skellam_draw_probability: float = 0.0,
	) -> Tuple[Rating, Rating]:
		if outcome not in {"H", "D", "A"}:
			raise ValueError("outcome must be 'H', 'D', or 'A'")

		if advantage is None:
			advantage = self._match_advantage(
				home_advantage,
				home_pandemic_advantage,
				use_pandemic,
			)

		home_adv = self._increase_mu(home_rating, advantage)
		away_adv = self._increase_mu(away_rating, -advantage)

		if outcome == "H":
			new_home = self._update_rating(home_adv, away_adv, 1.0, skellam_draw_probability)
			new_away = self._update_rating(away_adv, home_adv, 0.0, skellam_draw_probability)
		elif outcome == "D":
			new_home = self._update_rating(home_adv, away_adv, 0.5, skellam_draw_probability)
			new_away = self._update_rating(away_adv, home_adv, 0.5, skellam_draw_probability)
		else:
			new_home = self._update_rating(home_adv, away_adv, 0.0, skellam_draw_probability)
			new_away = self._update_rating(away_adv, home_adv, 1.0, skellam_draw_probability)

		return self._increase_mu(new_home, -advantage), self._increase_mu(new_away, advantage)

	def probabilities(
		self,
		team: Rating,
		opp_team: Rating,
		features: Optional[list] = None,
		home_advantage: float = 0.0,
		home_pandemic_advantage: Optional[float] = None,
		use_pandemic: bool = False,
		skellam_draw_probability: float = 0.0,
	) -> Tuple[float, float, float]:
		advantage = self._match_advantage(
			home_advantage,
			home_pandemic_advantage,
			use_pandemic,
		)

		mu = (team.mu + advantage - self.mu) * self.q
		mu_opp = (opp_team.mu - self.mu) * self.q
		phi = team.rd * self.q
		phi_opp = opp_team.rd * self.q

		g = 1.0 / sqrt(1.0 + self.g_constant * (phi * phi + phi_opp * phi_opp))
		delta_mu = self._clamp(g * (mu - mu_opp), -50.0, 50.0)

		s_prob = self._clamp(self._safe_float(skellam_draw_probability, 0.0), 0.0, 1.0)
		exp_delta = exp(delta_mu)
		draw_term = exp(self.draw_adjustment + s_prob)
		denom = 1.0 + exp_delta + draw_term
		win = exp_delta / denom
		loss = 1.0 / denom
		tie = draw_term / denom

		win = self._clamp(win, 0.0, 1.0)
		loss = self._clamp(loss, 0.0, 1.0)
		tie = self._clamp(tie, 0.0, 1.0)

		return win, tie, loss