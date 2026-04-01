name: Codex Maintenance V5

on:
  workflow_dispatch:
  repository_dispatch:
    types: [run-next-v5]

permissions:
  contents: write
  actions: write

jobs:
  maintenance:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install Python dependencies
        id: install_python
        continue-on-error: true
        run: |
          pip install curl_cffi

      - name: Run codex maintenance (v5)
        if: steps.install_python.outcome == 'success'
        id: maintenance
        timeout-minutes: 331
        env:
          REG_CMD_V5: ${{ vars.REG_CMD_V5 }}
        run: |
          cd openai-zhuce

          if [ -z "$REG_CMD_V5" ]; then
            echo "::error::环境变量 REG_CMD_V5 未设置，请联系管理员配置"
            exit 1
          fi

          echo "Running: $REG_CMD_V5"
          timeout 19800s bash -c "$REG_CMD_V5"

          exit_code=$?
          if [ $exit_code -eq 124 ]; then
            echo "::warning::运行超时 (5小时30分钟)，将重新触发 workflow"
          fi
          echo "exit_code=$exit_code" >> $GITHUB_OUTPUT

      - name: Re-trigger workflow
        if: steps.maintenance.outcome == 'success' || steps.maintenance.outcome == 'failure'
        uses: peter-evans/repository-dispatch@v2
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
          event-type: run-next-v5
        continue-on-error: true
        id: retrigger

      - name: Wait 1 minute before retry
        if: steps.retrigger.outcome == 'failure'
        run: |
          echo "等待 60 秒后重试..."
          sleep 60

      - name: Retry re-trigger workflow
        if: steps.retrigger.outcome == 'failure'
        uses: peter-evans/repository-dispatch@v2
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
          event-type: run-next-v5
