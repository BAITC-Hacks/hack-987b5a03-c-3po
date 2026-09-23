import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  ApiError,
  artifactUrl,
  createRun,
  executeRun,
  getCase,
  getClusters,
  getEventHistory,
  getHealth,
  getNode,
  getRun,
  getTopNodes,
  importDataset,
  resetDemo,
  subscribeToEvents,
  validGid,
} from "./api";
import type {
  ArtifactName,
  ClusterSummary,
  DatasetImportResult,
  Gid,
  NodeDetail,
  NodeSummary,
  ReviewCase,
  RunEvent,
  RunRecord,
  RunStatus,
} from "./types";
import {
  LANGUAGE_KEY,
  languageOptions,
  locale,
  t,
  translateEvidence,
  translateTrace,
  type Language,
} from "./i18n";

const SESSION_KEY = "aml-agent-demo-run-id";
const GraphView = lazy(() => import("./GraphView"));
const FINAL_STATES: RunStatus[] = [
  "completed",
  "failed",
  "verification_failed",
];
const STATUS_ORDER: RunStatus[] = [
  "created",
  "validated",
  "graph_ready",
  "analyzed",
  "clustered",
  "classified",
  "ranked",
  "case_created",
  "exported",
  "verified",
  "completed",
];
const WORKFLOW = [
  { status: "validated", label: "Validate dataset" },
  { status: "analyzed", label: "Analyze network" },
  { status: "ranked", label: "Prioritize targets" },
  { status: "case_created", label: "Create review case" },
  { status: "completed", label: "Verify results" },
] as const;
const NAV = [
  { id: "overview", label: "Overview" },
  { id: "workflow", label: "Run workflow" },
  { id: "targets", label: "Priority queue" },
  { id: "network", label: "Network explorer" },
  { id: "case", label: "Review case" },
] as const;
type SectionId = (typeof NAV)[number]["id"];
const ARTIFACTS: { name: ArtifactName; label: string; description: string }[] =
  [
    {
      name: "nodes_roles.csv",
      label: "Node assessments",
      description: "All 2,248 clients",
    },
    {
      name: "clusters.csv",
      label: "Cluster overview",
      description: "Community evidence",
    },
    {
      name: "top_nodes.csv",
      label: "Priority queue",
      description: "Ranked review targets",
    },
    {
      name: "aml_review_report.xlsx",
      label: "Excel review report",
      description: "Formatted tables for manual review",
    },
    {
      name: "audit.json",
      label: "Audit record",
      description: "Run and verification",
    },
  ];

function statusIndex(status: RunStatus): number {
  return STATUS_ORDER.indexOf(status);
}

function formatScore(score: number): string {
  return `${Math.round(score * 100)}%`;
}

function formatKzt(value: number, language: Language): string {
  return `${new Intl.NumberFormat(locale[language], { maximumFractionDigits: 0 }).format(value)} KZT`;
}

function describeError(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The request could not be completed.";
}

function prettyStatus(status: string): string {
  return status.replaceAll("_", " ");
}

function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const shared = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true as const,
  };
  if (name === "arrow")
    return (
      <svg {...shared}>
        <path d="M5 12h14m-6-6 6 6-6 6" />
      </svg>
    );
  if (name === "check")
    return (
      <svg {...shared}>
        <path d="m5 12 4 4L19 6" />
      </svg>
    );
  if (name === "download")
    return (
      <svg {...shared}>
        <path d="M12 3v12m-4-4 4 4 4-4M4 17v3h16v-3" />
      </svg>
    );
  if (name === "upload")
    return (
      <svg {...shared}>
        <path d="M12 16V4m-4 4 4-4 4 4M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" />
      </svg>
    );
  if (name === "search")
    return (
      <svg {...shared}>
        <circle cx="11" cy="11" r="7" />
        <path d="m16 16 4 4" />
      </svg>
    );
  if (name === "close")
    return (
      <svg {...shared}>
        <path d="M5 5 19 19M19 5 5 19" />
      </svg>
    );
  if (name === "network")
    return (
      <svg {...shared}>
        <circle cx="5" cy="12" r="2" />
        <circle cx="19" cy="5" r="2" />
        <circle cx="19" cy="19" r="2" />
        <path d="m7 11 10-5M7 13l10 5" />
      </svg>
    );
  if (name === "shield")
    return (
      <svg {...shared}>
        <path d="M12 2 4 5v6c0 5.2 3.5 8.7 8 11 4.5-2.3 8-5.8 8-11V5l-8-3Z" />
        <path d="m9 12 2 2 4-4" />
      </svg>
    );
  if (name === "spark")
    return (
      <svg {...shared}>
        <path d="M12 2 9.5 9.5 2 12l7.5 2.5L12 22l2.5-7.5L22 12l-7.5-2.5L12 2Z" />
      </svg>
    );
  return null;
}

export default function App() {
  const mobileNavRef = useRef<HTMLElement>(null);
  const clickedSectionRef = useRef<{ id: SectionId; at: number } | null>(null);
  const [language, setLanguage] = useState<Language>(() => {
    const saved = window.localStorage?.getItem(LANGUAGE_KEY);
    return saved === "ru" || saved === "kk" || saved === "en" ? saved : "en";
  });
  const [activeSection, setActiveSection] = useState<SectionId>(() => {
    const hash = window.location.hash.slice(1);
    return NAV.find((item) => item.id === hash)?.id ?? "overview";
  });
  const [runId, setRunId] = useState<string | null>(() =>
    sessionStorage.getItem(SESSION_KEY),
  );
  const [run, setRun] = useState<RunRecord | null>(null);
  const [health, setHealth] = useState<"checking" | "online" | "offline">(
    "checking",
  );
  const [liveConfigured, setLiveConfigured] = useState(false);
  const [runMode, setRunMode] = useState<RunRecord["mode"]>("demo");
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [seedInput, setSeedInput] = useState("");
  const [importedDataset, setImportedDataset] =
    useState<DatasetImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [traceInterrupted, setTraceInterrupted] = useState(false);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [targets, setTargets] = useState<NodeSummary[]>([]);
  const [clusters, setClusters] = useState<ClusterSummary[]>([]);
  const [reviewCase, setReviewCase] = useState<ReviewCase | null>(null);
  const [query, setQuery] = useState("");
  const [queryError, setQueryError] = useState<string | null>(null);
  const [selectedGid, setSelectedGid] = useState<Gid | null>(null);
  const [nodeDetail, setNodeDetail] = useState<NodeDetail | null>(null);
  const [nodeError, setNodeError] = useState<string | null>(null);
  const [radius, setRadius] = useState<1 | 2>(1);
  const [activeCluster, setActiveCluster] = useState<number | null>(null);
  const ended = run ? FINAL_STATES.includes(run.status) : false;
  const completed =
    run?.status === "completed" && run.verification_status === "passed";

  useEffect(() => {
    if (completed) {
      setError((current) =>
        current === "Verified assessments are not yet available."
          ? null
          : current,
      );
    }
  }, [completed]);

  useEffect(() => {
    window.localStorage?.setItem(LANGUAGE_KEY, language);
    document.documentElement.lang = language;
    document.title = `AML Agent · ${t("Analyst workspace", language)}`;
  }, [language]);

  useEffect(() => {
    const syncSection = () => {
      const hash = window.location.hash.slice(1);
      setActiveSection(NAV.find((item) => item.id === hash)?.id ?? "overview");
    };
    window.addEventListener("hashchange", syncSection);
    return () => window.removeEventListener("hashchange", syncSection);
  }, []);

  useEffect(() => {
    const syncSectionFromScroll = () => {
      const sections = NAV.slice(1).map((item) => ({
        id: item.id,
        rect: document.getElementById(item.id)?.getBoundingClientRect(),
      }));
      if (sections.some((section) => !section.rect?.height)) return;

      const pageHeight = Math.max(
        document.documentElement.scrollHeight,
        document.body.scrollHeight,
      );
      const atBottom = window.scrollY + window.innerHeight >= pageHeight - 4;
      const clicked = clickedSectionRef.current;
      if (clicked) {
        const top = document
          .getElementById(clicked.id)
          ?.getBoundingClientRect().top;
        const anchorTop =
          clicked.id === "overview" ? 0 : window.innerWidth <= 900 ? 125 : 90;
        const reached =
          (clicked.id === "overview" && window.scrollY <= 4) ||
          (clicked.id === "case" && atBottom) ||
          (top !== undefined && Math.abs(top - anchorTop) <= 32);
        if (!reached && performance.now() - clicked.at < 1500) return;
        clickedSectionRef.current = null;
      }

      if (atBottom) {
        setActiveSection("case");
        return;
      }
      const activationLine = Math.min(window.innerHeight * 0.35, 260);
      let visible: SectionId = "overview";
      for (const section of sections) {
        if (section.rect && section.rect.top <= activationLine) {
          visible = section.id;
        }
      }
      setActiveSection(visible);
    };
    const cancelClickedSection = () => {
      clickedSectionRef.current = null;
    };
    const cancelOnScrollKey = (event: KeyboardEvent) => {
      if (
        [
          "ArrowDown",
          "ArrowUp",
          "PageDown",
          "PageUp",
          "Home",
          "End",
          " ",
        ].includes(event.key)
      ) {
        cancelClickedSection();
      }
    };
    window.addEventListener("scroll", syncSectionFromScroll, { passive: true });
    window.addEventListener("wheel", cancelClickedSection, { passive: true });
    window.addEventListener("touchstart", cancelClickedSection, {
      passive: true,
    });
    window.addEventListener("keydown", cancelOnScrollKey);
    syncSectionFromScroll();
    return () => {
      window.removeEventListener("scroll", syncSectionFromScroll);
      window.removeEventListener("wheel", cancelClickedSection);
      window.removeEventListener("touchstart", cancelClickedSection);
      window.removeEventListener("keydown", cancelOnScrollKey);
    };
  }, []);

  useEffect(() => {
    const nav = mobileNavRef.current;
    if (!nav || window.innerWidth > 900 || typeof nav.scrollBy !== "function")
      return;
    const selected = nav.querySelector<HTMLElement>(".nav-link.active");
    if (!selected) return;
    const navRect = nav.getBoundingClientRect();
    const selectedRect = selected.getBoundingClientRect();
    const leftGap = selectedRect.left - navRect.left;
    const rightGap = selectedRect.right - navRect.right;
    const delta =
      leftGap < 12 ? leftGap - 12 : rightGap > -12 ? rightGap + 12 : 0;
    if (delta) nav.scrollBy({ left: delta, behavior: "smooth" });
  }, [activeSection, language]);

  const checkHealth = useCallback(async () => {
    setHealth("checking");
    try {
      const result = await getHealth();
      setHealth(result.backend_ready ? "online" : "offline");
      setLiveConfigured(result.live_configured);
    } catch {
      setHealth("offline");
      setLiveConfigured(false);
    }
  }, []);

  useEffect(() => {
    void checkHealth();
  }, [checkHealth]);

  useEffect(() => {
    if (!runId) return;
    let alive = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const current = await getRun(runId);
        if (alive) {
          setRun(current);
          if (
            FINAL_STATES.includes(current.status) &&
            !current.executing &&
            timer !== undefined
          )
            window.clearInterval(timer);
        }
      } catch (caught) {
        if (!alive) return;
        if (caught instanceof ApiError && caught.status === 404) {
          sessionStorage.removeItem(SESSION_KEY);
          setRunId(null);
          setRun(null);
          setError(
            "The previous run is no longer available. Start a new demo run.",
          );
        } else setError(describeError(caught));
      }
    };
    void poll();
    timer = window.setInterval(poll, 1500);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [runId]);

  useEffect(() => {
    if (!runId) return;
    if (ended) {
      let alive = true;
      void getEventHistory(runId)
        .then((history) => {
          if (alive) setEvents(history);
        })
        .catch((caught) => {
          if (alive) setError(describeError(caught));
        });
      return () => {
        alive = false;
      };
    }
    setTraceInterrupted(false);
    return subscribeToEvents(
      runId,
      (event) => {
        setEvents((previous) => {
          if (
            previous.some(
              (item) =>
                item.event_id === event.event_id ||
                item.sequence === event.sequence,
            )
          )
            return previous;
          return [...previous, event].sort((a, b) => a.sequence - b.sequence);
        });
        setTraceInterrupted(false);
      },
      () => setTraceInterrupted(true),
    );
  }, [runId, ended]);

  useEffect(() => {
    if (!runId || !completed) return;
    let alive = true;
    void getClusters(runId)
      .then((page) => {
        if (alive) setClusters(page.items);
      })
      .catch((caught) => {
        if (alive) setError(describeError(caught));
      });
    void getTopNodes(runId)
      .then((page) => {
        if (alive) setTargets(page.items);
      })
      .catch((caught) => {
        if (alive) setError(describeError(caught));
      });
    return () => {
      alive = false;
    };
  }, [runId, completed]);

  useEffect(() => {
    // The case ID is stored before exports and independent verification finish.
    // The API intentionally rejects case reads until that verification passes.
    if (!run?.case_id || !completed) return;
    let alive = true;
    void getCase(run.case_id)
      .then((item) => {
        if (alive) setReviewCase(item);
      })
      .catch((caught) => {
        if (alive) setError(describeError(caught));
      });
    return () => {
      alive = false;
    };
  }, [run?.case_id, completed]);

  useEffect(() => {
    if (!runId || !selectedGid) return;
    let alive = true;
    setNodeDetail(null);
    setNodeError(null);
    void getNode(runId, selectedGid, radius)
      .then((item) => {
        if (alive) setNodeDetail(item);
      })
      .catch((caught) => {
        if (alive) setNodeError(describeError(caught));
      });
    return () => {
      alive = false;
    };
  }, [runId, selectedGid, radius]);

  const selectNode = useCallback((gid: Gid) => {
    setSelectedGid(gid);
    setRadius(1);
    setQueryError(null);
  }, []);

  async function startRun() {
    setBusy(true);
    setError(null);
    try {
      const created = await createRun(runMode, importedDataset?.dataset_id);
      sessionStorage.setItem(SESSION_KEY, created.run_id);
      setRun(created);
      setRunId(created.run_id);
      setEvents([]);
      setTargets([]);
      setClusters([]);
      setReviewCase(null);
      setSelectedGid(null);
      void executeRun(created.run_id).catch((caught) =>
        setError(describeError(caught)),
      );
    } catch (caught) {
      setError(describeError(caught));
      void checkHealth();
    } finally {
      setBusy(false);
    }
  }

  async function uploadDataset() {
    const seeds = seedInput
      .split(/[\s,;]+/)
      .map((value) => value.trim())
      .filter(Boolean);
    if (!uploadFiles.length) {
      setError("Choose at least one CSV file.");
      return;
    }
    if (!seeds.length || !seeds.every(validGid)) {
      setError("Enter one or more full decimal seed GIDs.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setImportedDataset(await importDataset(uploadFiles, seeds));
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    setError(null);
    try {
      if (run?.mode !== "live") {
        for (let attempt = 0; attempt < 3; attempt += 1) {
          try {
            await resetDemo();
            break;
          } catch (caught) {
            if (
              !(caught instanceof ApiError) ||
              caught.code !== "RUN_BUSY" ||
              attempt === 2
            )
              throw caught;
            await new Promise((resolve) => window.setTimeout(resolve, 350));
          }
        }
      }
      sessionStorage.removeItem(SESSION_KEY);
      setRunId(null);
      setRun(null);
      setEvents([]);
      setTargets([]);
      setClusters([]);
      setReviewCase(null);
      setSelectedGid(null);
      setActiveCluster(null);
      setImportedDataset(null);
      setUploadFiles([]);
      setSeedInput("");
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const gid = query.trim();
    if (!validGid(gid)) {
      setQueryError(
        "Enter a full decimal GID. Letters and punctuation are not accepted.",
      );
      return;
    }
    if (!runId || !completed) {
      setQueryError(
        "Node evidence is available after the run passes verification.",
      );
      return;
    }
    selectNode(gid);
  }

  const selectedCluster =
    clusters.find((cluster) => cluster.cluster_id === activeCluster) ??
    clusters[0];
  const label = (text: string) => t(text, language);
  const pretty = (value: string) => label(prettyStatus(value));
  const activeLabel =
    NAV.find((item) => item.id === activeSection)?.label ?? "Overview";
  const navLinks = NAV.map((item) => (
    <a
      className={`nav-link ${activeSection === item.id ? "active" : ""}`}
      href={`#${item.id}`}
      key={item.id}
      onClick={() => {
        clickedSectionRef.current = { id: item.id, at: performance.now() };
        setActiveSection(item.id);
      }}
      aria-current={activeSection === item.id ? "location" : undefined}
    >
      <span className="nav-dot" />
      {label(item.label)}
    </a>
  ));

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <Icon name="network" size={24} />
          </span>
          <span>
            AML <strong>Agent</strong>
            <small>{label("Analyst workspace")}</small>
          </span>
        </div>
        <div className="sidebar-section-label">{label("Workspace")}</div>
        <nav aria-label={label("Workspace sections")}>{navLinks}</nav>
        <div className="sidebar-bottom">
          <div className="sidebar-callout">
            <Icon name="shield" size={18} />
            <div>
              <strong>{label("Decision support")}</strong>
              <p>
                {label(
                  "Roles and scores are review hypotheses, never findings of guilt.",
                )}
              </p>
            </div>
          </div>
          <span className="sidebar-version">
            {label("Bundled July 2026 · ruleset v1")}
          </span>
        </div>
      </aside>

      <main className="main-content" id="overview">
        <header className="topbar">
          <div className="breadcrumb">
            {label("Workspace")} <span>/</span> {label(activeLabel)}
          </div>
          <div className="topbar-right">
            <label className="language-picker">
              <span aria-hidden="true">{language.toUpperCase()}</span>
              <select
                aria-label={label("Select language")}
                value={language}
                onChange={(event) =>
                  setLanguage(event.target.value as Language)
                }
              >
                {languageOptions.map((option) => (
                  <option value={option.value} key={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </header>
        <nav
          ref={mobileNavRef}
          className="mobile-nav"
          aria-label={label("Workspace sections")}
        >
          {navLinks}
        </nav>
        <div className="content-wrap">
          <section className="hero">
            <div className="hero-copy">
              <span className="eyebrow">
                <span className="eyebrow-line" />{" "}
                {label("Network intelligence · July 2026")}
              </span>
              <h1>
                {label("From transfer network")}
                <br />
                <em>{label("to review decision.")}</em>
              </h1>
              <p>
                {label(
                  "Starting with 81 seed clients, trace July transfers across four hops. See which accounts merit review first, why, and what evidence supports that choice.",
                )}
              </p>
              {!runId && (
                <div className="dataset-import">
                  <div className="dataset-import-head">
                    <span className="dataset-import-kicker">
                      {label("Custom dataset")}
                    </span>
                    <strong>{label("Analyze your own data")}</strong>
                    <span>
                      {label("Required columns: src, dst, date, sum_kzt")}
                    </span>
                  </div>
                  <div className="dataset-import-fields">
                    <label
                      className={`dataset-file-drop ${uploadFiles.length ? "has-files" : ""}`}
                      htmlFor="dataset-files"
                      onDragOver={(event) => event.preventDefault()}
                      onDrop={(event) => {
                        event.preventDefault();
                        const files = Array.from(event.dataTransfer.files).filter(
                          (file) => file.name.toLowerCase().endsWith(".csv"),
                        );
                        setUploadFiles(files);
                        setImportedDataset(null);
                      }}
                    >
                      <input
                        id="dataset-files"
                        className="dataset-file-input"
                        type="file"
                        accept=".csv,text/csv"
                        multiple
                        disabled={busy}
                        onChange={(event) => {
                          setUploadFiles(Array.from(event.target.files ?? []));
                          setImportedDataset(null);
                        }}
                      />
                      <span className="dataset-file-icon">
                        {uploadFiles.length ? (
                          <Icon name="check" size={19} />
                        ) : (
                          <Icon name="upload" size={19} />
                        )}
                      </span>
                      <span className="dataset-file-copy">
                        <strong>
                          {uploadFiles.length
                            ? `${uploadFiles.length} ${label(uploadFiles.length === 1 ? "file selected" : "files selected")}`
                            : label("Choose CSV files")}
                        </strong>
                        <small>
                          {uploadFiles.length
                            ? uploadFiles.map((file) => file.name).join(", ")
                            : label("or drop them here · up to 12 files / 25 MB")}
                        </small>
                      </span>
                    </label>
                    <label className="dataset-seeds">
                      <span>{label("Seed client GIDs")}</span>
                      <input
                        value={seedInput}
                        disabled={busy}
                        placeholder={label("e.g. 9007199254740993, 9007199254740995")}
                        aria-label={label("Seed GIDs")}
                        onChange={(event) => {
                          setSeedInput(event.target.value);
                          setImportedDataset(null);
                        }}
                      />
                      <small>
                        {label("Separate IDs with commas, spaces, or new lines")}
                      </small>
                    </label>
                  </div>
                  <div className="dataset-import-footer">
                    <span className="dataset-local-note">
                      <Icon name="shield" size={15} />
                      {label("Files stay in this local workspace")}
                    </span>
                    <button
                      className="dataset-prepare-button"
                      type="button"
                      onClick={() => void uploadDataset()}
                      disabled={
                        busy || !uploadFiles.length || !seedInput.trim()
                      }
                    >
                      {busy ? <Spinner /> : <Icon name="arrow" size={17} />}
                      {label("Prepare dataset")}
                    </button>
                  </div>
                  {importedDataset && (
                    <div className="dataset-import-success" role="status">
                      <Icon name="check" size={16} />
                      <strong>{label("Dataset ready")}</strong>
                      <span>
                        {importedDataset.n_files} {label("files")} ·{" "}
                        {importedDataset.n_transactions} {label("transactions")} ·{" "}
                        {importedDataset.n_nodes} {label("clients")}
                      </span>
                    </div>
                  )}
                </div>
              )}
              <div className="hero-actions">
                {!runId && (
                  <div
                    className="mode-toggle"
                    aria-label={label("Agent provider mode")}
                  >
                    <button
                      type="button"
                      className={runMode === "demo" ? "selected" : ""}
                      onClick={() => setRunMode("demo")}
                      disabled={busy}
                    >
                      {label("Demo")}
                    </button>
                    <button
                      type="button"
                      className={runMode === "live" ? "selected" : ""}
                      onClick={() => setRunMode("live")}
                      disabled={busy || !liveConfigured}
                      title={
                        liveConfigured
                          ? label("Use the configured OpenAI provider")
                          : label(
                              "Configure OPENAI_API_KEY to enable live mode",
                            )
                      }
                    >
                      {label("OpenAI live")}
                    </button>
                  </div>
                )}
                <button
                  className="primary-button"
                  onClick={() => void startRun()}
                  disabled={
                    busy ||
                    health !== "online" ||
                    Boolean(runId) ||
                    (runMode === "live" && !liveConfigured)
                  }
                >
                  {busy ? <Spinner /> : <Icon name="spark" size={18} />}
                  {runId
                    ? label("Run started")
                    : runMode === "live"
                      ? label("Start OpenAI run")
                      : label(
                          importedDataset
                            ? "Analyze imported dataset"
                            : "Start bundled demo",
                        )}{" "}
                  {!busy && !runId && <Icon name="arrow" size={17} />}
                </button>
                {runId && (
                  <button
                    className="text-button on-dark"
                    onClick={() => void reset()}
                    disabled={busy || !ended || Boolean(run?.executing)}
                  >
                    {label(run?.mode === "live" ? "New run" : "Reset demo")}
                  </button>
                )}
              </div>
              <div className="hero-note">
                <span className="note-icon">
                  <Icon name="check" size={14} />
                </span>
                {label(
                  runMode === "live"
                    ? liveConfigured
                      ? "OpenAI chooses tools; analytics and verification remain deterministic"
                      : "Add OPENAI_API_KEY to the ignored .env file to enable live mode"
                    : "Real analytics, local case creation, independent verification",
                )}
              </div>
            </div>
            <div className="hero-art" aria-hidden="true">
              <div className="orbit orbit-one" />
              <div className="orbit orbit-two" />
              <div className="orbit orbit-three" />
              <div className="hero-core">
                <Icon name="network" size={42} />
              </div>
              <span className="art-node node-a" />
              <span className="art-node node-b" />
              <span className="art-node node-c" />
              <span className="art-node node-d" />
              <div className="art-label">
                {label("Evidence")} <span>→</span> {label("Action")}
              </div>
            </div>
          </section>

          {error && (
            <div className="alert error-alert" role="alert">
              <strong>{label("Action needs attention")}</strong>
              <span>{label(error)}</span>
              <button
                aria-label={label("Dismiss error")}
                onClick={() => setError(null)}
              >
                <Icon name="close" size={16} />
              </button>
            </div>
          )}
          {health === "offline" && !error && (
            <div className="alert action-alert" role="status">
              <span>
                {label("API is unavailable. Check the local server and retry.")}
              </span>
              <button className="retry-link" onClick={() => void checkHealth()}>
                {label("Retry")}
              </button>
            </div>
          )}

          <section
            className="dataset-strip"
            aria-label={label("Bundled dataset")}
          >
            <div className="strip-intro">
              <span className="strip-icon">
                <Icon name="network" size={19} />
              </span>
              <div>
                <strong>{label("Bundled transaction network")}</strong>
                <span>{label("July 1–31, 2026 · four-hop sample")}</span>
              </div>
            </div>
            <div className="strip-stat">
              <strong>2,248</strong>
              <span>{label("clients")}</span>
            </div>
            <div className="strip-stat">
              <strong>3,119</strong>
              <span>{label("directed edges")}</span>
            </div>
            <div className="strip-stat">
              <strong>4,840</strong>
              <span>{label("transactions")}</span>
            </div>
            <div className="strip-stat">
              <strong>81</strong>
              <span>{label("seed clients")}</span>
            </div>
          </section>

          {run?.warnings.length ? (
            <div className="run-warnings">
              <strong>{label("Data limitations recorded")}</strong>
              <ul>
                {run.warnings.map((warning, index) => (
                  <li key={`${index}-${warning}`}>{label(warning)}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {run?.result?.status === "needs_user_action" && (
            <div className="alert action-alert" role="status">
              <strong>{label("Run needs input")}</strong>
              <span>{label(run.result.recommended_next_step)}</span>
            </div>
          )}

          <section className="section-block" id="workflow">
            <div className="section-heading">
              <div>
                <span className="section-kicker">
                  01 / {label("Execution")}
                </span>
                <h2>{label("Run workflow")}</h2>
                <p>
                  {label(
                    "One run validates the data, computes evidence, creates a case, and checks every export.",
                  )}
                </p>
              </div>
              {run && (
                <span
                  className={`status-badge ${completed ? "success" : ended ? "danger" : "running"}`}
                >
                  {!ended && <span className="pulse-dot" />}
                  {pretty(run.status)}
                </span>
              )}
            </div>
            <div className="workflow-layout">
              <div className="workflow-card">
                <div className="card-title-row">
                  <div>
                    <span className="small-label">
                      {label("Analysis pipeline")}
                    </span>
                    <h3>
                      {!run
                        ? label("Ready to analyze")
                        : completed
                          ? label("Verified run complete")
                          : ended
                            ? label("Run stopped")
                            : label("Analysis in progress")}
                    </h3>
                  </div>
                  {!ended && run && <Spinner />}
                </div>
                <div className="workflow-steps">
                  {WORKFLOW.map((step, index) => {
                    const done =
                      run &&
                      statusIndex(run.status) >= statusIndex(step.status);
                    const current =
                      run &&
                      !ended &&
                      statusIndex(run.status) < statusIndex(step.status) &&
                      (index === 0 ||
                        statusIndex(run.status) >=
                          statusIndex(WORKFLOW[index - 1].status));
                    return (
                      <div
                        className={`workflow-step ${done ? "done" : current ? "current" : ""}`}
                        key={step.status}
                      >
                        <div className="step-marker">
                          {done ? (
                            <Icon name="check" size={13} />
                          ) : (
                            String(index + 1).padStart(2, "0")
                          )}
                        </div>
                        <div>
                          <strong>{label(step.label)}</strong>
                          <span>
                            {done
                              ? label("Completed")
                              : current
                                ? label("In progress")
                                : label("Waiting")}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="run-foot">
                  {run ? (
                    <>
                      {label("Run")} <code>{run.run_id}</code>
                    </>
                  ) : (
                    label("Start the bundled demo to create a real run.")
                  )}
                </div>
              </div>
              <div className="trace-card">
                <div className="card-title-row">
                  <div>
                    <span className="small-label">
                      {label("Safe execution trace")}
                    </span>
                    <h3>{label("What the agent did")}</h3>
                  </div>
                  <span className="live-indicator">
                    <span />
                    {ended
                      ? label("Finished")
                      : run
                        ? label("Live")
                        : label("Idle")}
                  </span>
                </div>
                <div className="trace-list" aria-live="polite">
                  {events.length === 0 ? (
                    <div className="empty-trace">
                      <Icon name="spark" size={26} />
                      <strong>{label("No events yet")}</strong>
                      <span>
                        {label(
                          "Tool activity and verification will appear here when a run starts.",
                        )}
                      </span>
                    </div>
                  ) : (
                    events.map((event) => (
                      <div
                        className={`trace-item ${event.kind}`}
                        key={event.event_id}
                      >
                        <span className="trace-icon">
                          {event.kind === "failed" || event.kind === "warning"
                            ? "!"
                            : event.kind === "tool_started"
                              ? "→"
                              : "✓"}
                        </span>
                        <div>
                          <strong>
                            {translateTrace(event.summary, language)}
                          </strong>
                          <span>
                            {event.tool_name
                              ? pretty(event.tool_name)
                              : pretty(event.kind)}{" "}
                            ·{" "}
                            {new Date(event.created_at).toLocaleTimeString(
                              locale[language],
                            )}
                          </span>
                        </div>
                      </div>
                    ))
                  )}
                </div>
                {traceInterrupted && !ended && (
                  <div className="trace-footnote">
                    {label(
                      "Live trace disconnected. Status still refreshes automatically.",
                    )}
                  </div>
                )}
              </div>
            </div>
          </section>

          <section className="section-block" id="targets">
            <div className="section-heading">
              <div>
                <span className="section-kicker">
                  02 / {label("Priorities")}
                </span>
                <h2>{label("Review targets")}</h2>
                <p>
                  {label(
                    "Deterministic ranking with concrete evidence and explicit uncertainty.",
                  )}
                </p>
              </div>
              <span className="section-count">
                {targets.length
                  ? `${targets.length} ${label("targets")}`
                  : label("Awaiting ranking")}
              </span>
            </div>
            <div className="table-card">
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>{label("Rank")}</th>
                      <th>{label("Client GID")}</th>
                      <th>{label("Role")}</th>
                      <th>{label("Priority")}</th>
                      <th>{label("Evidence & warnings")}</th>
                      <th aria-label={label("Details")} />
                    </tr>
                  </thead>
                  <tbody>
                    {targets.map((target, index) => (
                      <tr
                        key={target.gid}
                        onClick={() => selectNode(target.gid)}
                      >
                        <td>
                          <span className="rank-number">
                            {String(target.rank ?? index + 1).padStart(2, "0")}
                          </span>
                        </td>
                        <td>
                          <code className="gid-code">{target.gid}</code>
                        </td>
                        <td>
                          <span className={`role-pill role-${target.role}`}>
                            {pretty(target.role)}
                          </span>
                        </td>
                        <td>
                          <span className="score-label">
                            {formatScore(target.priority_score)}
                          </span>
                          <span className="score-track">
                            <span
                              style={{
                                width: `${Math.min(100, Math.max(0, target.priority_score * 100))}%`,
                              }}
                            />
                          </span>
                        </td>
                        <td>
                          <span className="evidence-text">
                            {translateEvidence(target.evidence, language)}
                          </span>
                          {target.uncertainty_flags?.length > 0 && (
                            <span className="warning-text">
                              {target.uncertainty_flags.map(pretty).join(" · ")}
                            </span>
                          )}
                        </td>
                        <td>
                          <button
                            className="row-action"
                            aria-label={`${label("View client")} ${target.gid}`}
                            onClick={(event) => {
                              event.stopPropagation();
                              selectNode(target.gid);
                            }}
                          >
                            <Icon name="arrow" size={16} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {targets.length === 0 && (
                <div className="table-empty">
                  {label(
                    "The priority queue appears after independent verification passes.",
                  )}
                </div>
              )}
              <div className="table-footnote">
                {label(
                  "Priority and role scores measure rule strength. They are not probabilities of criminal activity.",
                )}
              </div>
            </div>
          </section>

          <section className="section-block" id="network">
            <div className="section-heading">
              <div>
                <span className="section-kicker">03 / {label("Explore")}</span>
                <h2>{label("Network explorer")}</h2>
                <p>
                  {label(
                    "Focus on a cluster, then inspect a bounded directed ego graph.",
                  )}
                </p>
              </div>
            </div>
            <div className="explore-layout">
              <div className="clusters-card">
                <div className="card-title-row">
                  <div>
                    <span className="small-label">
                      {label("Community overview")}
                    </span>
                    <h3>{label("Clusters")}</h3>
                  </div>
                  <span className="muted-count">
                    {clusters.length
                      ? `${clusters.length} ${label("shown")}`
                      : label("Pending")}
                  </span>
                </div>
                <div className="cluster-list">
                  {clusters.length === 0 ? (
                    <p className="empty-copy">
                      {label(
                        "Cluster summaries appear after independent verification passes.",
                      )}
                    </p>
                  ) : (
                    clusters.map((cluster) => (
                      <button
                        className={`cluster-option ${selectedCluster?.cluster_id === cluster.cluster_id ? "selected" : ""}`}
                        onClick={() => setActiveCluster(cluster.cluster_id)}
                        key={cluster.cluster_id}
                      >
                        <span className="cluster-avatar">
                          {String(cluster.cluster_id).padStart(2, "0")}
                        </span>
                        <span>
                          <strong>
                            {label("Cluster")} {cluster.cluster_id}
                          </strong>
                          <small>
                            {cluster.n_nodes} {label("clients")} ·{" "}
                            {cluster.n_seed} {label("seeds")}
                          </small>
                        </span>
                        <Icon name="arrow" size={15} />
                      </button>
                    ))
                  )}
                </div>
                {selectedCluster && (
                  <div className="cluster-summary">
                    <span className="small-label">
                      {label("Selected cluster")}
                    </span>
                    <p>{label(selectedCluster.hypothesis)}</p>
                    <div className="cluster-metric">
                      <span>{label("Internal turnover")}</span>
                      <strong>
                        {formatKzt(selectedCluster.sum_kzt_internal, language)}
                      </strong>
                    </div>
                    <div className="cluster-gids">
                      {selectedCluster.top_gids.slice(0, 3).map((gid) => (
                        <button key={gid} onClick={() => selectNode(gid)}>
                          GID {gid}
                          <Icon name="arrow" size={12} />
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
              <div className="network-card">
                <div className="card-title-row">
                  <div>
                    <span className="small-label">
                      {label("Directed ego graph")}
                    </span>
                    <h3>
                      {selectedGid
                        ? `${label("Client")} ${selectedGid}`
                        : label("Select a client")}
                    </h3>
                  </div>
                  {selectedGid && (
                    <div
                      className="radius-toggle"
                      aria-label={label("Graph radius")}
                    >
                      <button
                        className={radius === 1 ? "selected" : ""}
                        onClick={() => setRadius(1)}
                      >
                        1 {label("hop")}
                      </button>
                      <button
                        className={radius === 2 ? "selected" : ""}
                        onClick={() => setRadius(2)}
                      >
                        2 {label("hops")}
                      </button>
                    </div>
                  )}
                </div>
                <form className="search-form" onSubmit={search}>
                  <Icon name="search" size={18} />
                  <input
                    aria-label={label("Search full client GID")}
                    inputMode="numeric"
                    placeholder={label("Search full client GID")}
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                  />
                  <button type="submit">{label("Search")}</button>
                </form>
                {queryError && (
                  <p className="field-error">{label(queryError)}</p>
                )}
                {nodeError && <p className="field-error">{label(nodeError)}</p>}
                {nodeDetail ? (
                  <>
                    <Suspense
                      fallback={
                        <div className="graph-placeholder">
                          {label("Loading graph…")}
                        </div>
                      }
                    >
                      <GraphView
                        graph={nodeDetail.ego_graph}
                        focusGid={nodeDetail.gid}
                        onSelect={selectNode}
                        language={language}
                      />
                    </Suspense>
                    <div className="graph-foot">
                      <span>
                        {label(
                          "Arrows follow transfer direction · Click a node for detail",
                        )}
                      </span>
                      {nodeDetail.ego_graph.truncated && (
                        <strong>{label("View bounded by server")}</strong>
                      )}
                    </div>
                  </>
                ) : (
                  <div className="graph-placeholder">
                    <div className="placeholder-orbit">
                      <Icon name="network" size={35} />
                    </div>
                    <strong>
                      {selectedGid
                        ? label("Loading network slice…")
                        : label("A focused view of the network")}
                    </strong>
                    <span>
                      {label(
                        "Select a ranked target, a cluster GID, or search by full identifier.",
                      )}
                    </span>
                  </div>
                )}
              </div>
            </div>
          </section>

          <section className="section-block" id="case">
            <div className="section-heading">
              <div>
                <span className="section-kicker">04 / {label("Outcome")}</span>
                <h2>{label("From signal to action")}</h2>
                <p>
                  {label(
                    "The agent creates a local review case, then independently verifies the output bundle.",
                  )}
                </p>
              </div>
              <span
                className={`verification-badge ${completed ? "verified" : run?.verification_status === "failed" ? "failed" : ""}`}
              >
                <Icon name="shield" size={16} />
                {completed
                  ? label("Verified")
                  : run?.verification_status === "failed"
                    ? label("Verification failed")
                    : label("Awaiting verification")}
              </span>
            </div>
            <div className="outcome-layout">
              <div className="case-card before">
                <span className="case-step">{label("Before")}</span>
                <h3>{label("Unreviewed transfer graph")}</h3>
                <p>
                  {label(
                    "Thousands of clients and transfers, without an ordered analyst queue or case record.",
                  )}
                </p>
                <div className="case-visual">
                  <span>2,248 {label("clients")}</span>
                  <Icon name="arrow" size={22} />
                  <span>{label("Needs triage")}</span>
                </div>
              </div>
              <div className={`case-card after ${reviewCase ? "active" : ""}`}>
                <span className="case-step">{label("After")}</span>
                <h3>
                  {reviewCase
                    ? label(reviewCase.title)
                    : label("Review case pending")}
                </h3>
                <p>
                  {reviewCase
                    ? `${reviewCase.target_gids.length} ${label("ranked clients captured for analyst review.")}`
                    : label(
                        "A local case is created from the ranked target snapshot.",
                      )}
                </p>
                <div className="case-visual">
                  <span>
                    {reviewCase
                      ? pretty(reviewCase.status)
                      : label("No case yet")}
                  </span>
                  <span className="case-id">
                    {reviewCase ? `ID ${reviewCase.case_id.slice(0, 8)}` : "—"}
                  </span>
                </div>
              </div>
            </div>
            <div className="download-card">
              <div>
                <span className="small-label">
                  {label("Verified export bundle")}
                </span>
                <h3>{label("Evidence you can inspect")}</h3>
                <p>
                  {label(
                    "Downloads unlock only when the run completes with passed verification.",
                  )}
                </p>
              </div>
              <div className="download-list">
                {ARTIFACTS.map((artifact) =>
                  completed && runId ? (
                    <a
                      className="download-link"
                      href={artifactUrl(runId, artifact.name)}
                      key={artifact.name}
                      download={artifact.name}
                    >
                      <span>
                        <strong>{label(artifact.label)}</strong>
                        <small>{label(artifact.description)}</small>
                      </span>
                      <Icon name="download" size={18} />
                    </a>
                  ) : (
                    <div className="download-link disabled" key={artifact.name}>
                      <span>
                        <strong>{label(artifact.label)}</strong>
                        <small>{label(artifact.description)}</small>
                      </span>
                      <Icon name="download" size={18} />
                    </div>
                  ),
                )}
              </div>
            </div>
          </section>
          <footer>
            {label(
              "AML Agent · Analyst decision support · All actions stay local to the review workspace.",
            )}
          </footer>
        </div>
      </main>

      {selectedGid && (
        <div className="drawer-backdrop" onClick={() => setSelectedGid(null)}>
          <aside
            className="detail-drawer"
            role="dialog"
            aria-modal="true"
            aria-label={`${label("Client")} ${selectedGid} ${label("detail")}`}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="drawer-head">
              <span className="section-kicker">{label("Client evidence")}</span>
              <button
                aria-label={label("Close client detail")}
                onClick={() => setSelectedGid(null)}
              >
                <Icon name="close" size={19} />
              </button>
            </div>
            {nodeDetail ? (
              <>
                <h2>{nodeDetail.gid}</h2>
                <div className="drawer-badges">
                  <span className={`role-pill role-${nodeDetail.role}`}>
                    {pretty(nodeDetail.role)}
                  </span>
                  {nodeDetail.is_seed && (
                    <span className="tag">{label("Seed client")}</span>
                  )}
                </div>
                <div className="drawer-score">
                  <div>
                    <span>{label("Priority score")}</span>
                    <strong>{formatScore(nodeDetail.priority_score)}</strong>
                  </div>
                  <div>
                    <span>{label("Role match")}</span>
                    <strong>{formatScore(nodeDetail.role_score)}</strong>
                  </div>
                </div>
                <div className="drawer-section">
                  <span className="small-label">{label("Rule evidence")}</span>
                  <p>{translateEvidence(nodeDetail.evidence, language)}</p>
                </div>
                <div className="drawer-section">
                  <span className="small-label">
                    {label("Observed network")}
                  </span>
                  <dl className="metric-grid">
                    <div>
                      <dt>{label("In degree")}</dt>
                      <dd>{nodeDetail.in_degree}</dd>
                    </div>
                    <div>
                      <dt>{label("Out degree")}</dt>
                      <dd>{nodeDetail.out_degree}</dd>
                    </div>
                    <div>
                      <dt>{label("Incoming")}</dt>
                      <dd>{formatKzt(nodeDetail.in_kzt, language)}</dd>
                    </div>
                    <div>
                      <dt>{label("Outgoing")}</dt>
                      <dd>{formatKzt(nodeDetail.out_kzt, language)}</dd>
                    </div>
                    <div>
                      <dt>{label("Cluster")}</dt>
                      <dd>{nodeDetail.cluster_id}</dd>
                    </div>
                    <div>
                      <dt>{label("Depth")}</dt>
                      <dd>{nodeDetail.depth}</dd>
                    </div>
                  </dl>
                </div>
                <div className="drawer-section">
                  <span className="small-label">{label("Uncertainty")}</span>
                  {nodeDetail.uncertainty_flags.length ? (
                    <ul className="uncertainty-list">
                      {nodeDetail.uncertainty_flags.map((flag) => (
                        <li key={flag}>{pretty(flag)}</li>
                      ))}
                    </ul>
                  ) : (
                    <p>{label("No node-specific flags recorded.")}</p>
                  )}
                  {nodeDetail.truncated_by_depth && (
                    <p className="boundary-note">
                      {label(
                        "At the depth boundary, missing outgoing transfers do not establish a terminal role.",
                      )}
                    </p>
                  )}
                </div>
                <p className="drawer-disclaimer">
                  {label(
                    "This assessment supports analyst review and is not a finding of wrongdoing.",
                  )}
                </p>
              </>
            ) : (
              <div className="drawer-loading">
                {nodeError ? (
                  label(nodeError)
                ) : (
                  <>
                    {label("Loading client evidence…")} <Spinner />
                  </>
                )}
              </div>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}
