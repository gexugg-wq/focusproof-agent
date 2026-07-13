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
