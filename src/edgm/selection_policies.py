"""Operator selection policies for the shared Corrected EIDETIC runner."""

import math

POLICIES = ("random", "beta-ts", "discounted-ts")

def new_state(policy, arms, ts, gamma=0.97):
    if not arms or len(set(arms)) != len(arms):
        raise ValueError("arms must be nonempty and unique")
    if policy == "beta-ts":
        return ts.new_beta_state(arms)
    if policy == "random":
        return {"policy_id": "random_uniform_v1", "arms": {arm: {"updates": 0} for arm in arms},
                "updates": 0}
    if policy == "discounted-ts":
        if not 0 < gamma < 1 or not math.isfinite(gamma):
            raise ValueError("discount gamma must be finite and in (0,1)")
        return {"policy_id": "discounted_beta_ts_v1", "gamma": gamma,
                "arms": {arm: {"alpha": 1.0, "beta": 1.0, "updates": 0} for arm in arms},
                "updates": 0}
    raise ValueError(f"unknown policy: {policy}")

def pick(policy, state, count, rng, ts):
    if not 1 <= count <= len(state["arms"]):
        raise ValueError("arm count outside available arms")
    if policy == "beta-ts":
        return ts.beta_topk(state, count, rng)
    if policy == "random":
        if state.get("policy_id") != "random_uniform_v1":
            raise ValueError("not a random-policy state")
        return rng.sample(list(state["arms"]), count)
    if policy == "discounted-ts":
        if state.get("policy_id") != "discounted_beta_ts_v1":
            raise ValueError("not a discounted-TS state")
        scores = [(rng.betavariate(float(v["alpha"]), float(v["beta"])), arm)
                  for arm, v in state["arms"].items()]
        return [arm for _, arm in sorted(scores, reverse=True)[:count]]
    raise ValueError(f"unknown policy: {policy}")

def update(policy, state, chosen, reward, ts):
    if policy == "beta-ts":
        ts.beta_update(state, chosen, reward)
        return
    if not chosen or len(set(chosen)) != len(chosen) or any(arm not in state["arms"] for arm in chosen):
        raise ValueError("invalid chosen arms")
    if not math.isfinite(reward) or not 0 <= reward <= 1:
        raise ValueError("reward must be finite and in [0,1]")
    if policy == "random":
        if state.get("policy_id") != "random_uniform_v1":
            raise ValueError("not a random-policy state")
        for arm in chosen:
            state["arms"][arm]["updates"] += 1
    elif policy == "discounted-ts":
        if state.get("policy_id") != "discounted_beta_ts_v1":
            raise ValueError("not a discounted-TS state")
        gamma = state["gamma"]
        if not 0 < gamma < 1:
            raise ValueError("invalid discount gamma")
        # Forget evidence, not the Beta(1,1) prior, once per accepted mutant.
        for arm in state["arms"].values():
            arm["alpha"] = 1.0 + gamma * (arm["alpha"] - 1.0)
            arm["beta"] = 1.0 + gamma * (arm["beta"] - 1.0)
        share = reward / len(chosen)
        for name in chosen:
            arm = state["arms"][name]
            arm["alpha"] += share
            arm["beta"] += 1.0 - share
            arm["updates"] += 1
    else:
        raise ValueError(f"unknown policy: {policy}")
    state["updates"] += 1
