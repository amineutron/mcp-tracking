# Journal des sessions

## 2026-08-25 -- tracking-c1 : fiabilite, metriques, deploiement

- Objectif : durcir le service tracking et decouper le serveur monolithique.
- Fait : server.py scinde en api.py / mutations.py / metrics.py / sim.py ; storage.py durci
  (ecriture atomique, verrou, cache mtime, purge TTL) ; poller.py sans secret en dur
  (credentials/*.cred via _secret) ; etat deplace dans ~/.local/state/tracking (XDG).
- Fait : tests/test_metrics.py (65 tests verts au total), Makefile (test/smoke/deploy/ui),
  deploy.sh idempotent, install.sh simplifie, README reecrit pour la nouvelle architecture.
- Fait : unites systemd basculees en services systeme, verifiees actives ; depot pousse sur
  GitHub en prive (marouabah/mcp-tracking, remote origin).
- Ouvert : rien de bloquant ; CLAUDE.md absent du projet (regles heritees du hub dev/).
