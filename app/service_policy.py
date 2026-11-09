"""Local administrator-attested policy; this command grants no execution capability."""
import argparse
from app.database import SessionLocal
from app.incidents import configure_service, observe_service, utcnow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--monitor-id', required=True, type=int)
    parser.add_argument('--project')
    parser.add_argument('--service')
    parser.add_argument('--observation-only', action='store_true')
    parser.add_argument('--authorize-restart', action='store_true', help='Dry-run recommendation only; default denied')
    parser.add_argument('--observed-state', choices=['unknown', 'running', 'stopped'], default='unknown')
    args = parser.parse_args()
    if args.observation_only:
        if args.project or args.service or args.authorize_restart:
            parser.error('Observation-only cannot change identity or authorization')
        observe_service(SessionLocal, args.monitor_id, args.observed_state)
        print('Administrator-attested observation refreshed; configuration revision unchanged.')
        return
    if not args.project or not args.service:
        parser.error('--project and --service are required for configuration')
    configure_service(SessionLocal, args.monitor_id, args.project, args.service,
        authorized=args.authorize_restart, observed_state=args.observed_state,
        observed_at=utcnow() if args.observed_state != 'unknown' else None)
    print('Policy revision recorded. No recovery action is enabled or executed.')


if __name__ == '__main__':
    main()
