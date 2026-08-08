# FocusProof Contracts Placeholder

Future contracts should store only lightweight proof data:

- sessionId
- learner
- domain
- score
- effectiveMinutes
- summaryHash
- proofVersion
- createdAt

The following must not be stored on-chain:

- full notes;
- images;
- audio;
- video;
- raw code;
- full Agent conversations;
- private evidence.

## Monad learning task

`monad-learning-task/` is an optional, isolated teaching-contract package.
`MonadLearningCounter` records only each caller's counter and emits the
corresponding state-transition event. It does not store FocusProof scores,
reviews, streaks, tokens, NFTs, notes, or identity claims.

Use Node.js 22.13 or newer:

    cd contracts/monad-learning-task
    npm install
