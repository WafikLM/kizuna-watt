# Travailler ensemble sur Kizuna Watt

## Premiere installation (chaque membre, une seule fois)
1. Installer Git (https://git-scm.com) et Python 3.11 ou plus recent.
2. Dire a Git qui vous etes (votre vrai nom, l'e-mail de votre compte GitHub) :
   `git config --global user.name "Votre Nom"` puis `git config --global user.email "vous@exemple.com"`
3. Recuperer le projet : `git clone https://github.com/PROPRIETAIRE/kizuna-watt.git` puis `cd kizuna-watt`
4. Installer les bibliotheques : `pip install -r requirements.txt`
5. Lancer la demonstration (donnees simulees) : `python tools/demo.py run` puis ouvrir http://127.0.0.1:8030
   Les comptes de demonstration sont ecrits dans `data/demo/demo_comptes.md`.

## Regles d'or
- **Jamais sur Git** : cles API (Brevo `xkeysib-...`), mots de passe, dossier `data/`, fichiers `.db`, `reward.key`, `.env`. Le `.gitignore` les bloque ; ne le contournez pas (`git add -f`).
- Une cle partie sur GitHub par erreur = **a revoquer tout de suite** chez le fournisseur (la supprimer du depot ne suffit pas).
- Ne travaillez **pas directement sur `main`** : une branche par tache.
- Un petit commit clair vaut mieux qu'un gros commit confus.

## Le geste de tous les jours
```
git switch main
git pull                          # recuperer le travail des autres
git switch -c ma-fonctionnalite   # nouvelle branche (une par tache)
# ... modifier les fichiers ...
git add -A
git status                        # VERIFIER qu'aucun secret ni fichier .db n'est liste
git commit -m "Ajoute la page X"
git push -u origin ma-fonctionnalite
```
Puis sur GitHub : **Compare & pull request** -> un camarade relit -> **Merge**. Ensuite : `git switch main` puis `git pull`.

## En cas de conflit
Deux personnes ont modifie la meme ligne. Git marque `<<<<<<<` / `=======` / `>>>>>>>` dans le fichier : gardez la bonne version, supprimez les marques, puis `git add` et `git commit`. En cas de doute, demandez avant de forcer quoi que ce soit (`--force` est interdit sur `main`).

## Configuration locale (non partagee)
Pour tester l'e-mail reel, chacun cree sa cle dans **son** compte Brevo et la met dans sa propre fenetre de commande (voir README, section « Module SMS / E-mail »). Ne la collez jamais dans le chat ni dans un fichier du projet.
