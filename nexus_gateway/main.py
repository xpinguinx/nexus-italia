from __future__ import annotations

import argparse
import asyncio

from .config import load_config
from .logging_utils import setup_logging
from .service import GatewayService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    setup_logging(config.runtime.log_level)
    asyncio.run(GatewayService(config).start())


if __name__ == "__main__":
    main()
