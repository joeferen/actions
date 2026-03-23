name: Codex Maintenance V3

on:
  workflow_dispatch:
    inputs:
      min_accounts:
        description: '账号数量阈值'
        required: false
      quota_threshold:
        description: '额度不足删除阈值(%)'
        required: false
      domain_index:
        description: '邮箱域名索引'
        required: false
  repository_dispatch:
    types: [run-next-v3]

permissions:
  contents: write
  actions: write

jobs:
  maintenance:
    runs-on: ubuntu-latest
    
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
      
      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'
      
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      
      - name: Install Python dependencies
        run: |
          pip install curl_cffi
      
      - name: Run codex maintenance (V3)
        id: maintenance
        timeout-minutes: 6
        continue-on-error: true
        env:
          BASE_URL: ${{ vars.CODEX_BASE_URL }}
          TOKEN: ${{ vars.CODEX_TOKEN }}
          DEFAULT_MIN_ACCOUNTS: ${{ vars.DEFAULT_MIN_ACCOUNTS || '100' }}
          DEFAULT_QUOTA_THRESHOLD: ${{ vars.DEFAULT_QUOTA_THRESHOLD || '20' }}
          DEFAULT_DOMAIN_INDEX: ${{ vars.DEFAULT_DOMAIN_INDEX || '0' }}
          TIMEOUT_SECONDS: "330"
        run: |
          cd openai-zhuce
          
          # 使用 timeout 命令限制运行时间为 5 分 30 秒 (330 秒)
          timeout 330s node codex_maintenance.js \
            "${{ github.event.inputs.min_accounts || env.DEFAULT_MIN_ACCOUNTS }}" \
            "${{ github.event.inputs.quota_threshold || env.DEFAULT_QUOTA_THRESHOLD }}" \
            "${{ env.BASE_URL }}" \
            "${{ env.TOKEN }}" \
            "${{ github.event.inputs.domain_index || env.DEFAULT_DOMAIN_INDEX }}" \
            "1800" \
            "openai_register_v3.py" \
            "50"
          
          exit_code=$?
          if [ $exit_code -eq 124 ]; then
            echo "::warning::运行超时 (5分30秒)，将重新触发 workflow"
          fi
          echo "exit_code=$exit_code" >> $GITHUB_OUTPUT
      
      - name: Re-trigger workflow
        uses: peter-evans/repository-dispatch@v2
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
          event-type: run-next-v3
