name: Duo stock check
on:
  schedule:
    - cron: "3-58/5 * * * *"
  workflow_dispatch:
    inputs:
      test:
        description: "Send a fake in-stock alert"
        type: boolean
        default: false
concurrency:
  group: duo-check
permissions:
  contents: write
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: main
      - run: pip install requests
      - run: python check.py
        env:
          PART: ${{ secrets.PART }}
          ZIP: ${{ secrets.ZIP }}
          TEST: ${{ inputs.test }}
      - name: Save log
        if: ${{ !inputs.test }}
        run: |
          git config user.name "duo-bot"
          git config user.email "duo-bot@users.noreply.github.com"
          git add -A
          git diff --cached --quiet && exit 0
          git commit -m "Update stock log"
          for i in 1 2 3; do
            git pull --rebase -X theirs origin main && git push && exit 0
            git rebase --abort 2>/dev/null || true
            sleep 5
          done
          exit 1
