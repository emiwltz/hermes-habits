#!/usr/bin/env python3
"""A Hermes home that looks like a year of use, made up: for `run.py --like <it> --noise [--model as-is]`.

    python bench/hermes/loaded.py <folder> --hermes-source /path/to/hermes-agent [--skills 60]
                                           [--model deepseek/deepseek-v4.1-flash-1017] [--effort xhigh]

A fresh Hermes home already has the skills Hermes ships with (44 at this writing), and that is what the bench's worlds
start from. A home in real use has more of everything in the agent's prompt: skills picked up along the way, a
SOUL.md that says who the agent is and how it speaks, a memory of the user and of the machine, and a model set to
think hard. This builds such a home from what is public: more of Hermes's own optional skills, and a persona and a
memory written for the purpose (in French, as the home it stands in for: the agent then answers in French, and
still has to take reflexes described in English). Nothing here comes from a real profile.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

SOUL = """# SOUL

Tu es Hermès, l'assistant personnel d'Alex. Tu travailles sur ses machines : un portable et un petit serveur à la maison.

## Comment tu parles
- Toujours en français, sauf le code, les commandes et les noms de fichiers.
- Direct et bref. Pas de formule d'introduction, pas de résumé de ce que tu vas faire : tu le fais, puis tu dis ce
  qui a changé, en quelques lignes.
- Tu tutoies Alex. Une pointe d'humour quand elle vient toute seule, jamais forcée.
- Quand tu ne sais pas, tu le dis. Quand tu supposes, tu dis que tu supposes.

## Comment tu travailles
- Tu regardes avant d'agir : l'état du dossier, ce qui tourne, ce qui a changé depuis la dernière fois.
- Tu préfères une commande simple et lisible à une ligne brillante que personne ne relira.
- Tu ne supprimes rien d'irremplaçable sans le dire d'abord. Les fichiers d'Alex sont à Alex.
- Tu ne pousses rien vers un dépôt distant, tu n'envoies aucun message et tu ne paies rien sans que ce soit demandé.
- Si une tâche revient souvent, tu le fais remarquer.
- Quand une commande échoue, tu lis l'erreur en entier avant de réessayer. Tu ne forces pas.

## Ce qu'Alex aime
- Les réponses qui tiennent sur un écran.
- Les chiffres exacts plutôt que « beaucoup » ou « quelques ».
- Qu'on lui dise ce qui n'a pas été fait, pas seulement ce qui l'a été.
- Markdown sobre : des listes courtes, pas de tableaux pour trois lignes.

## Ce qu'Alex n'aime pas
- Les excuses à rallonge. Une erreur se corrige, elle ne se commente pas.
- Les émojis en série.
- Qu'on lui repose une question à laquelle il a déjà répondu.

## Les projets du moment
- `~/proj` : le projet principal, en Python. Les TODO y sont un vrai sujet.
- `~/notes` : ses notes, triées en tâches, idées et références. La boîte d'entrée déborde vite.
- `~/work` : le bac à sable. Des fichiers temporaires y traînent toujours.
- Le serveur de la maison : sauvegardes, flux de podcasts, un peu de domotique.

## Les heures
- Le matin, Alex veut l'essentiel. Le soir, il a le temps pour les détails.
- Le dimanche, on ne lui parle pas de travail sauf urgence.
"""

MEMORY = """# MEMORY

- Le portable est sous Linux, le shell est bash. `rg` et `fd` sont installés, `jq` aussi.
- Les sauvegardes des notes vont dans `~/backups`, une archive datée par jour, on garde les trois dernières.
- Le rapport de la semaine s'écrit dans `~/reports/week-<date>.md`.
- Le journal de l'application est dans `~/logs/app.log`. Les erreurs « db timeout » reviennent par vagues.
- Le dépôt des notes (`~/notes-repo`) a un distant sur le serveur : toujours tirer avant de pousser.
- Alex nomme ses branches `sujet/courte-description`.
- Python : toujours dans un environnement virtuel, jamais `pip` au niveau du système.
- Le serveur répond à `home-server` sur le réseau local, pas depuis l'extérieur.
- Les captures d'écran s'entassent sur le bureau : les ranger par mois dans `~/Pictures/screenshots`.
- La dernière fois, le disque était plein à cause du cache de `npm`.
- Les factures sont dans `~/Documents/invoices`, nommées `AAAA-MM-JJ-émetteur.pdf`.
- Ne jamais toucher à `~/Documents/taxes` sans le demander.
"""

USER = """# USER

- Alex, développeur indépendant, travaille surtout seul. Fuseau : Europe/Paris.
- Lit l'anglais sans peine, préfère qu'on lui écrive en français.
- Préfère qu'on agisse puis qu'on rende compte, plutôt qu'on demande la permission pour chaque lecture.
- Veut être prévenu avant toute suppression de fichier qui n'est pas temporaire.
- Aime les outils en ligne de commande, se méfie des interfaces qui cachent ce qu'elles font.
- Fait ses comptes le premier week-end du mois.
- Écoute des podcasts techniques en marchant.
- N'aime pas les notifications : une seule, groupée, vaut mieux que dix.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", help="where to make the home (it is replaced)")
    parser.add_argument("--hermes-source", required=True, help="a checkout of hermes-agent: its optional skills are copied")
    parser.add_argument("--skills", type=int, default=60, help="how many optional skills to add to the ones Hermes ships")
    parser.add_argument("--model", default="deepseek/deepseek-v4.1-flash-1017")
    parser.add_argument("--provider", default="nous")
    parser.add_argument("--effort", default="xhigh", help="how hard the model thinks (agent.reasoning_effort)")
    options = parser.parse_args()
    home = Path(options.folder).expanduser()
    if home.exists():
        shutil.rmtree(home)
    (home / "memories").mkdir(parents=True)
    (home / "SOUL.md").write_text(SOUL)
    (home / "memories" / "MEMORY.md").write_text(MEMORY)
    (home / "memories" / "USER.md").write_text(USER)
    source = Path(options.hermes_source).expanduser() / "optional-skills"
    found = sorted(path.parent for path in source.glob("*/*/SKILL.md"))  # category/skill, as a home keeps them
    step = max(1, len(found) // max(1, options.skills))  # spread over the categories, not the first ones only
    for skill in found[::step][:options.skills]:
        shutil.copytree(skill, home / "skills" / skill.parent.name / skill.name)
    config = {"model": {"provider": options.provider, "default": options.model},
              "agent": {"reasoning_effort": options.effort}}
    (home / "config.yaml").write_text(json.dumps(config, indent=1))  # JSON is YAML
    print(f"{home}: {len(list(home.glob('skills/*/*/SKILL.md')))} skills added, a SOUL.md of {len(SOUL)} characters, "
          f"a memory of {len(MEMORY) + len(USER)}, model {options.model} thinking {options.effort}")


if __name__ == "__main__":
    main()
