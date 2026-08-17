# Git accounts for this Mac

## Personal

- GitHub host alias: `github-tuadenu`
- GitHub account: `tuadenu`
- SSH key: `~/.ssh/id_ed25519_github_personal`
- Use for personal repos

## Work

- GitHub host alias: `github-viettrungnhat`
- GitHub account: `viettrungnhat`
- SSH key: `~/.ssh/id_ed25519_github_work`
- Use for work repos

## Current repo

- Remote URL: `git@github-tuadenu:tuadenu/texttomp3m4a-MacOs.git`
- Meaning: this repo uses the personal `tuadenu` account

## Useful commands

Check which account is active:

```bash
ssh -T git@github-tuadenu
ssh -T git@github-viettrungnhat
```

Set a repo to personal:

```bash
git remote set-url origin git@github-tuadenu:OWNER/REPO.git
```

Set a repo to work:

```bash
git remote set-url origin git@github-viettrungnhat:OWNER/REPO.git
```

