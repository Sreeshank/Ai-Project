"""Run from the project root: python -m backend.manage_vectors migrate|reindex|status."""
import argparse
import json

from backend import main, semantic
from backend.indexing import enqueue_all


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['migrate', 'reindex', 'status'])
    action = parser.parse_args().action
    main.initialize()
    if action == 'migrate':
        if not semantic.configured():
            parser.exit(1, 'Set DATABASE_URL and GEMINI_API_KEY in backend/.env first.\n')
        try:
            semantic.migrate()
        except Exception as exc:
            parser.exit(1, f'Migration failed ({type(exc).__name__}). Check database permissions and connectivity.\n')
        print('pgvector schema ready.')
    elif action == 'reindex':
        with main.connect() as db:
            enqueue_all(db)
        print('Reindex queued. The running backend processes the durable queue.')
    print(json.dumps(main.vector_index.status(), indent=2))


if __name__ == '__main__':
    cli()
