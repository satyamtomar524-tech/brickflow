"""Create a current-version copy of a fictional legacy ledger; preserve its source."""
import argparse
from pathlib import Path
import ledger


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    result = ledger.upgrade_copy(args.source, args.destination)
    print(f'Upgraded copy verified: {result}')
    print('The original database was not migrated.')
