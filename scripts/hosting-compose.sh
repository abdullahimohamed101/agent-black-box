# Sourced by hosting-upgrade.sh, hosting-backup.sh and hosting-restore-drill.sh: the ONE place that decides which compose files and
# profiles a hosted install uses (review P1-3: the scripts used to omit the observability overlay, so an upgrade silently removed the
# worker's metrics port). Run from the checkout (ABB_DIR) after `cd`.
#
#   ABB_COMPOSE_FILES        full explicit list (space separated); wins over everything below (rigs use it)
#   ABB_COMPOSE_EXTRA_FILES  appended to the computed list
#   ABB_OBSERVABILITY        1 | 0 | auto (default). auto = on when the env file defines ABB_OBS_UID, which guide step 9 writes
#                            when it enables the monitoring overlay.
# Sets: compose_files (string) and the array compose_profiles (--profile ... arguments).
abb_compose_resolve() {
  local envf="${ABB_COMPOSE_ENV_FILE:-.env}" obs="${ABB_OBSERVABILITY:-auto}"
  compose_files="${ABB_COMPOSE_FILES:-}"
  if [ -z "$compose_files" ]; then
    compose_files="docker-compose.yml deploy/hosting/compose.production.yml"
    if [ "$obs" = auto ]; then
      if grep -qs '^ABB_OBS_UID=' "$envf"; then obs=1; else obs=0; fi
    fi
    if [ "$obs" = 1 ]; then compose_files="$compose_files deploy/hosting/compose.observability.yml"; fi
    if [ -n "${ABB_COMPOSE_EXTRA_FILES:-}" ]; then compose_files="$compose_files $ABB_COMPOSE_EXTRA_FILES"; fi
  fi
  compose_profiles=(--profile ops)
  case " $compose_files " in *compose.observability.yml*) compose_profiles+=(--profile observability) ;; esac
}
