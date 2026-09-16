import os
from pathlib import Path
Path(os.environ['FA_PLUGIN_PROBE']).write_text('loaded', encoding='utf-8')
