import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

# Never let a test read the production goals model saved by the daily refresh.
os.environ["GOALS_MODEL_PATH"] = "/nonexistent/sportai-tests/goals_model.json"
# Nor write the squads cache of the scorer market (services/buteurs.py) next to the production state.
os.environ["SPORTAI_PLAYERS_CACHE"] = "/nonexistent/sportai-tests/effectifs"
