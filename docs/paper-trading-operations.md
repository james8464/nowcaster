# Paper trading operations

Paper trading uses simulated money at Alpaca. It is useful for finding software, reconciliation, slippage-model, and operating mistakes; it does **not** prove that a strategy will make money.

1. Create a separate Alpaca paper account/key pair. Never reuse live credentials.
2. In Nowcaster Settings, choose Paper and save the key ID and secret to Keychain.
3. Run Shadow first. It records order-shaped decisions but cannot create a fill.
4. Run the paper check with `python -m src.cli trading paper`. The endpoint is fixed to `paper-api.alpaca.markets`; there is no URL or live flag.
5. Check Execution Center. Any broker/local mismatch, stale input, unknown event, ambiguous submission, loss breaker, or invalid risk input blocks new orders.
6. Use Freeze to block admission and cancel open entries. Flatten is a separate destructive operation requiring the exact account suffix and phrase; acceptance of closing orders is not success until the broker reports zero positions.

Alpaca paper simulation can omit or simplify queue position, market impact, latency, borrow recalls, exchange faults, and other live behavior. Resetting the paper account or changing provider, feed, symbol, interval, strategy, weights, dataset, code, configuration, risk policy, or cost policy starts a new evidence cohort.

No command in the default workflow can enable live trading. Credentials must never be passed as command-line options or committed to Git.
