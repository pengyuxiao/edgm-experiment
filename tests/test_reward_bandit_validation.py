import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

def load():
    path=ROOT/"results/reward_bandit_validation/run_validation.py"
    spec=importlib.util.spec_from_file_location("reward_bandit_validation",path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class RewardBanditValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sim=load()
        cls.config=json.loads((ROOT/"results/reward_bandit_validation/config.json").read_text())

    def test_rank_mapping_matches_production_reward(self):
        from edgm.parameter_free_reward import score, EMPIRICAL_RANK_VERSION
        records,_=self.sim.offline.load_records()
        matrix=self.sim.offline.distances(records)
        refs=self.sim.reference_distributions(records,matrix,{"small":6})["small"]
        archive=[{"sha1":records[i]["view_id"],"vec":records[i]["vec"]} for i in range(6)]
        query=records[7]["vec"]
        actual=score(query,archive,version=EMPIRICAL_RANK_VERSION)
        self.assertAlmostEqual(self.sim.rank_reward(actual["raw_knn"],refs["loo_knn"]),
                               actual["reward01"])
        self.assertEqual(self.sim.rank_reward(0,refs["loo_knn"]),0)

    def test_pregenerated_outcomes_and_feedback_only_for_selected_arm(self):
        records,_=self.sim.offline.load_records()
        matrix=self.sim.offline.distances(records)
        refs=self.sim.reference_distributions(records,matrix,self.config["archive_sizes"])
        config={**self.config,"decision_rounds":12}
        cond=self.sim.conditions(config)[0]
        one=self.sim.potential_outcomes(cond,1000,8,config,refs)
        two=self.sim.potential_outcomes(cond,1000,8,config,refs)
        self.assertEqual(one,two)
        self.assertEqual(len(one),12)
        self.assertTrue(all(len(row["raw"])==8 and all(0<=x<=2 for x in row["raw"])
                            for row in one))
        a=self.sim.run_policy("A",one,refs,8,1000)
        b=self.sim.run_policy("B",one,refs,8,1000)
        random=self.sim.run_policy("Random",one,refs,8,1000)
        for result in (a,b,random):
            self.assertGreaterEqual(result["pseudo_regret"],0)
            self.assertGreaterEqual(result["realized_regret"],0)
            self.assertGreaterEqual(result["cumulative_raw"],0)
        self.assertEqual(a,self.sim.run_policy("A",one,refs,8,1000))
        self.assertIsNone(random["optimal_arm_identified"])
        self.assertTrue(0<=a["high_arm_rate"]<=1 and 0<=b["tie_fraction"]<=1)

    def test_predeclared_condition_count_and_confidence_calculation(self):
        self.assertEqual(len(self.sim.conditions(self.config)),14)
        self.assertEqual(self.sim.bootstrap_ci([1,2,3],100,73),
                         self.sim.bootstrap_ci([1,2,3],100,73))
        self.assertGreater(self.sim.sign_flip_paired([1,1,1],100,73),0)

if __name__=="__main__":
    unittest.main()
