"""Retrieval benchmark.   python scripts/eval_retrieval.py   (uses the configured database; ingest the handbook first)
Reports the fresh TEST set (never used for tuning), then the held-out DEVELOPMENT set (used to guide tuning), then the legacy set (not independent)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config, db
from app.services.evaluation_service import retrieval_eval

if __name__ == "__main__":
    db.init_engine()
    out = {}
    with db.session_scope() as s:
        for name in ("retrieval_test.json", "retrieval_heldout.json", "retrieval_legacy.json"):
            d = json.loads((config.ROOT / "eval" / name).read_text(encoding="utf-8"))
            r = retrieval_eval(s, d["cases"])
            out[name] = {"name": d["name"], **{k: v for k, v in r.items() if k != "misses"}, "misses": r["misses"]}
            print(f"\n{d['name']}\n  answerable={r['answerable']}  hit@1={r['hit_at_1_pct']}%  hit@3={r['hit_at_3_pct']}%  MRR={r['mrr']}  correct abstentions={r['correct_abstentions']}/{r['unanswerable']}")
            for m in r["misses"]:
                print(f"    miss: {m['query'][:70]!r} expected {m['expected']} got {m['got']}")

    (config.EVAL_RESULTS_DIR / "retrieval_latest.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("\nsaved eval/results/retrieval_latest.json")
