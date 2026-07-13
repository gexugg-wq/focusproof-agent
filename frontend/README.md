# FocusProof Frontend Placeholder

The future frontend should use Next.js and TypeScript.

Frontend responsibilities:

- collect user learning goals and session input;
- submit evidence;
- submit answers to agent follow-up questions;
- display review results;
- display the Build Log;
- support wallet connection for Web3 learning flows.

Frontend boundaries:

- it must not store LLM secrets;
- it must not directly calculate the final learning score;
- it must not directly verify Web3 evidence;
- it must not bypass the Agent Server to write database records.
