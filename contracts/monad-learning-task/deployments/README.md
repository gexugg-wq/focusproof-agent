# Monad Learning Counter Deployments

No live deployment is committed during deterministic implementation.

After the explicit human-wallet gate, the deployment script may create
`monad-testnet.json` only after receipt success and non-empty deployed
bytecode are verified. The artifact contains public data only:

- contract address;
- deployment transaction hash;
- chain ID;
- compiler version;
- source commit;
- ABI.

RPC credentials and deployer secrets must never appear in this directory.
