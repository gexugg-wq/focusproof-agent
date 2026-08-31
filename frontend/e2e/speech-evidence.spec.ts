import { expect, test, type Page, type Route } from "@playwright/test";

const sessionId = "sess_speech_e2e";
const acceptanceDir = "test-results/acceptance/ai6";
const rawTranscript = "  Raw transcript, unchanged.\nSecond line.  ";

const speechCapability = {
  capabilityId: "speech_transcription",
  schemaVersion: 1,
  enabled: true,
  formats: ["audio/webm;codecs=opus", "audio/wav", "audio/mpeg"],
  maxAudioBytes: 11 * 1024 * 1024,
  maxDurationSeconds: 120,
  languageHintsAccepted: ["auto", "zh", "en"],
  languageHintEffect: "metadata_only"
};

const baseSession = {
  sessionId,
  state: {
    sessionId,
    ownerUserId: "dev-anonymous-user",
    status: "running",
    goal: {
      domain: "general",
      title: "Speech evidence",
      goal: "Verify a candidate transcript before submitting evidence.",
      expectedOutput: "checked notes",
      plannedMinutes: 20
    },
    evidence: [],
    answers: {},
    observations: [],
    previousActions: [],
    reviewResult: null,
    adapterMode: "openhands-local-real",
    conversationId: "conv_speech_e2e",
    runtimeMode: "openhands-local-real"
  },
  view: { productCapabilities: [speechCapability] }
};

type MediaMode = "allowed" | "denied";

async function installFakeRecorder(page: Page, mode: MediaMode = "allowed") {
  await page.addInitScript(({ permissionMode }) => {
    const state = window as typeof window & {
      __speechE2E?: { getUserMediaCalls: number; recorderStarts: number; tracksStopped: number };
    };
    state.__speechE2E = { getUserMediaCalls: 0, recorderStarts: 0, tracksStopped: 0 };

    const track = { stop: () => { state.__speechE2E!.tracksStopped += 1; } };
    const stream = { getTracks: () => [track] } as unknown as MediaStream;
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: async () => {
          state.__speechE2E!.getUserMediaCalls += 1;
          if (permissionMode === "denied") {
            throw new DOMException("Permission denied", "NotAllowedError");
          }
          return stream;
        }
      }
    });

    class FakeMediaRecorder extends EventTarget {
      static isTypeSupported(type: string) {
        return type === "audio/webm;codecs=opus";
      }

      readonly mimeType: string;
      state: RecordingState = "inactive";

      constructor(_stream: MediaStream, options?: MediaRecorderOptions) {
        super();
        this.mimeType = options?.mimeType ?? "audio/webm;codecs=opus";
      }

      start() {
        state.__speechE2E!.recorderStarts += 1;
        this.state = "recording";
      }

      stop() {
        if (this.state !== "recording") return;
        this.state = "inactive";
        queueMicrotask(() => {
          this.dispatchEvent(new BlobEvent("dataavailable", {
            data: new Blob(["voice-bytes"], { type: this.mimeType })
          }));
          this.dispatchEvent(new Event("stop"));
        });
      }
    }

    Object.defineProperty(window, "MediaRecorder", {
      configurable: true,
      value: FakeMediaRecorder
    });
  }, { permissionMode: mode });
}

type MockApiOptions = {
  transcript?: string;
  transcriptionStatus?: number;
  transcriptionError?: { status: number; code: string; retryable?: boolean };
  onTranscription?: (route: Route, attempt: number) => Promise<void>;
};

async function mockApi(page: Page, options: MockApiOptions = {}) {
  let evidenceSubmissions = 0;
  let transcriptionAttempts = 0;
  let submittedText: string | null = null;

  await page.route("**/api/focusproof/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/transcriptions")) {
      transcriptionAttempts += 1;
      if (options.onTranscription) {
        await options.onTranscription(route, transcriptionAttempts);
        return;
      }
      if (options.transcriptionError) {
        const { status, code, retryable } = options.transcriptionError;
        await route.fulfill({ status, json: { code, ...(retryable === undefined ? {} : { retryable }) } });
        return;
      }
      const status = options.transcriptionStatus ?? 200;
      if (status !== 200) {
        await route.fulfill({ status, json: { code: "speech_unavailable", retryable: true } });
        return;
      }
      await route.fulfill({
        json: {
          requestId: `speech-${transcriptionAttempts}`,
          transcript: options.transcript ?? rawTranscript,
          provider: "dashscope",
          model: "qwen3-asr-flash"
        }
      });
      return;
    }
    if (path.endsWith("/evidence") && request.method() === "POST") {
      evidenceSubmissions += 1;
      const body = request.postDataJSON() as { textContent?: string };
      submittedText = body.textContent ?? null;
      await route.fulfill({ json: { sessionId, evidenceId: "ev_speech", syncPending: true } });
      return;
    }
    if (path.endsWith("/events")) {
      await route.fulfill({ json: { events: [] } });
      return;
    }
    if (path.endsWith("/reviews")) {
      await route.fulfill({ json: { reviews: [] } });
      return;
    }
    if (path.includes("/sessions/")) {
      await route.fulfill({ json: baseSession });
      return;
    }
    await route.fulfill({ status: 404, json: { code: "not_found" } });
  });

  return {
    evidenceSubmissions: () => evidenceSubmissions,
    transcriptionAttempts: () => transcriptionAttempts,
    submittedText: () => submittedText
  };
}

async function recordAndStop(page: Page) {
  await page.getByRole("button", { name: "Start recording" }).click();
  await expect(page.getByText(/Recording\.\.\. 0:00 \/ 2:00/)).toBeVisible();
  await page.getByRole("button", { name: "Stop recording" }).click();
}

test("records once, inserts the raw candidate, and submits evidence only after the user clicks", async ({ page }, testInfo) => {
  await installFakeRecorder(page);
  const api = await mockApi(page);
  await page.goto(`/sessions/${sessionId}`);

  const composer = page.getByRole("textbox", { name: "Learning evidence" });
  await expect(composer).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Start recording" })).toHaveCount(1);

  await recordAndStop(page);
  await expect(composer).toHaveValue(rawTranscript);
  expect(api.transcriptionAttempts()).toBe(1);
  expect(api.evidenceSubmissions()).toBe(0);

  await page.screenshot({
    path: `${acceptanceDir}/${testInfo.project.name}-speech-candidate.png`,
    fullPage: true
  });

  const verifiedText = `${rawTranscript}\n[verified by user]`;
  await composer.fill(verifiedText);
  expect(api.evidenceSubmissions()).toBe(0);
  await page.getByRole("button", { name: "Submit evidence" }).click();
  await expect(page.getByText(/Evidence saved, waiting for Agent sync/i)).toBeVisible();
  expect(api.evidenceSubmissions()).toBe(1);
  expect(api.submittedText()).toBe(verifiedText.trim());
});

test("shows a permission denial without attempting transcription", async ({ page }) => {
  await installFakeRecorder(page, "denied");
  const api = await mockApi(page);
  await page.goto(`/sessions/${sessionId}`);

  await page.getByRole("button", { name: "Start recording" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Microphone permission was denied" })).toBeVisible();
  expect(api.transcriptionAttempts()).toBe(0);
});

test("preserves existing composer text and refuses retry for a non-retryable failure", async ({ page }) => {
  await installFakeRecorder(page);
  const api = await mockApi(page, {
    transcriptionError: { status: 422, code: "transcription_ambiguous", retryable: false }
  });
  await page.goto(`/sessions/${sessionId}`);

  const composer = page.getByRole("textbox", { name: "Learning evidence" });
  await composer.fill("Existing user notes stay here.");
  await recordAndStop(page);
  await expect(page.getByRole("alert").filter({ hasText: "ambiguous" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry transcription" })).toHaveCount(0);
  await expect(composer).toHaveValue("Existing user notes stay here.");
  expect(api.transcriptionAttempts()).toBe(1);
  expect(api.evidenceSubmissions()).toBe(0);
});

test("retries the retained clip only after user action and submits only edited text", async ({ page }) => {
  let releaseRetry!: () => void;
  const retryCanFinish = new Promise<void>((resolve) => { releaseRetry = resolve; });
  const idempotencyKeys: string[] = [];
  await installFakeRecorder(page);
  const api = await mockApi(page, {
    onTranscription: async (route, attempt) => {
      idempotencyKeys.push(route.request().headers()["idempotency-key"] ?? "");
      if (attempt === 1) {
        await route.fulfill({ status: 504, json: { code: "transcription_timeout", retryable: true } });
        return;
      }
      await retryCanFinish;
      await route.fulfill({ json: { requestId: "speech-retry", transcript: rawTranscript, provider: "dashscope", model: "qwen3-asr-flash" } });
    }
  });
  await page.goto(`/sessions/${sessionId}`);

  const composer = page.getByRole("textbox", { name: "Learning evidence" });
  await recordAndStop(page);
  await expect(page.getByRole("button", { name: "Retry transcription" })).toBeVisible();
  await expect(composer).toHaveValue("");
  expect(api.transcriptionAttempts()).toBe(1);
  expect(api.evidenceSubmissions()).toBe(0);

  await page.getByRole("button", { name: "Retry transcription" }).evaluate((button) => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
  });
  await expect.poll(api.transcriptionAttempts).toBe(2);
  const counters = await page.evaluate(() => (window as typeof window & { __speechE2E?: { getUserMediaCalls: number; recorderStarts: number } }).__speechE2E);
  expect(counters).toMatchObject({ getUserMediaCalls: 1, recorderStarts: 1 });
  expect(api.evidenceSubmissions()).toBe(0);
  releaseRetry();
  await expect(composer).toHaveValue(rawTranscript);
  expect(idempotencyKeys).toHaveLength(2);
  expect(idempotencyKeys[0]).not.toBe("");
  expect(idempotencyKeys[1]).not.toBe(idempotencyKeys[0]);

  const editedText = `${rawTranscript}\n[edited after retry]`;
  await composer.fill(editedText);
  expect(api.evidenceSubmissions()).toBe(0);
  await page.getByRole("button", { name: "Submit evidence" }).click();
  await expect(page.getByText(/Evidence saved, waiting for Agent sync/i)).toBeVisible();
  expect(api.evidenceSubmissions()).toBe(1);
  expect(api.submittedText()).toBe(editedText.trim());
});

test("deduplicates start clicks and ignores a cancelled operation's late response after re-recording", async ({ page }) => {
  let releaseFirst!: () => void;
  const firstCanFinish = new Promise<void>((resolve) => { releaseFirst = resolve; });
  await installFakeRecorder(page);
  const api = await mockApi(page, {
    onTranscription: async (route, attempt) => {
      if (attempt === 1) {
        await firstCanFinish;
        await route.fulfill({ json: { requestId: "speech-stale", transcript: "STALE FIRST TRANSCRIPT", provider: "dashscope", model: "qwen3-asr-flash" } }).catch(() => undefined);
        return;
      }
      await route.fulfill({ json: { requestId: "speech-current", transcript: "Current second transcript", provider: "dashscope", model: "qwen3-asr-flash" } });
    }
  });
  await page.goto(`/sessions/${sessionId}`);

  await page.getByRole("button", { name: "Start recording" }).evaluate((button) => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
  });
  await expect(page.getByRole("button", { name: "Stop recording" })).toBeVisible();
  const counters = await page.evaluate(() => (window as typeof window & { __speechE2E?: { getUserMediaCalls: number; recorderStarts: number } }).__speechE2E);
  expect(counters).toMatchObject({ getUserMediaCalls: 1, recorderStarts: 1 });

  await page.getByRole("button", { name: "Stop recording" }).click();
  await expect.poll(api.transcriptionAttempts).toBe(1);
  await page.getByRole("button", { name: "Cancel recording" }).click();

  await recordAndStop(page);
  const composer = page.getByRole("textbox", { name: "Learning evidence" });
  await expect(composer).toHaveValue("Current second transcript");
  expect(api.transcriptionAttempts()).toBe(2);
  releaseFirst();
  await page.waitForTimeout(100);
  await expect(composer).toHaveValue("Current second transcript");
  expect(api.evidenceSubmissions()).toBe(0);
});
