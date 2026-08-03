export type RuntimeBudgetUsage = Record<string, number>;
export type RuntimeTimestamps = Record<string, string | null | undefined>;

export type RuntimeTask = {
  id: string;
  name: string;
  mode: string;
  goal: string;
  prompt?: string;
  schemaVersion: number;
  raw: Record<string, unknown>;
};

export type RuntimeSession = {
  status: string;
  supervisorSolverId: string | null;
  activeSolverCount: number;
  maxActiveWorkers: number;
  taskBudgetUsage: RuntimeBudgetUsage;
  stopReason: string | null;
  timestamps: RuntimeTimestamps;
  turnCount: number;
  maxTurns: number;
};

export type RuntimeTeam = {
  taskId: string;
  status: string;
  supervisorSolverId: string | null;
  maxActiveWorkers: number;
  maxTotalSolvers: number;
  activeSolverCount: number;
  solverIds: string[];
  version: number;
  timestamps: RuntimeTimestamps;
};

export type RuntimeSolver = {
  taskId: string;
  solverId: string;
  definitionId: string;
  orchestrationRole: string;
  specialties: string[];
  parentSolverId: string | null;
  assignedIntentId: string | null;
  status: string;
  currentSummary: string;
  modelSnapshot: Record<string, unknown>;
  skillSnapshot: Record<string, unknown>;
  capabilityBinding: Record<string, unknown>;
  budgetUsage: RuntimeBudgetUsage;
  timestamps: RuntimeTimestamps;
};

export type RuntimeIntent = {
  taskId: string;
  intentId: string;
  kind: string;
  title: string;
  objective: string;
  status: string;
  assignedSolverId: string | null;
  dependencies: string[];
  priority: number;
  budget: RuntimeBudgetUsage;
  createdAt: string;
  updatedAt: string;
};

export type RuntimeWorkerResult = {
  resultId: string;
  solverId: string;
  intentId: string;
  status: string;
  summary: string;
  artifactIds: string[];
  evidenceClaimIds: string[];
  knowledgeIds: string[];
  findingIds: string[];
  limitations: string[];
  budgetUsage: RuntimeBudgetUsage;
};

export type RuntimeGlobalPlan = Record<string, unknown> & { version?: number };

export type RuntimeKnowledgeItem = {
  knowledgeId: string;
  scope: string;
  targetId: string | null;
  status: string;
  kind: string;
  contentPreview: string;
  contentSha256: string;
  createdBySolverId: string | null;
  createdAt: string;
};

export type RuntimeArtifact = {
  artifactId: string;
  intentId: string | null;
  kind: string;
  mediaType: string | null;
  tool: string | null;
  target: string | null;
  sha256: string;
  createdAt: string;
};

export type RuntimeEvidenceClaim = {
  claimId: string;
  statementPreview: string;
  artifactId: string;
  locator: Record<string, unknown>;
  status: string;
  createdBySolverId: string | null;
  reviewedBySolverId: string | null;
  createdAt: string;
  reviewedAt: string | null;
};

export type RuntimeFinding = {
  findingId: string;
  title: string;
  descriptionPreview: string;
  target: string | null;
  severity: string;
  status: string;
  evidenceClaimIds: string[];
  createdBySolverId: string | null;
  createdAt: string;
  reviewedAt: string | null;
};

export type RuntimeAction = {
  actionId: string;
  solverId: string | null;
  intentId: string | null;
  capability: string;
  target: string;
  risk: string;
  effect: Record<string, unknown>;
  arguments: Record<string, unknown>;
  status: string;
  summary: string;
  artifactIds: string[];
  createdAt: string;
  updatedAt: string;
};

export type RuntimeApproval = {
  approvalId: string;
  solverId: string;
  intentId: string | null;
  actionId: string;
  action: Record<string, unknown>;
  risk: string;
  effect: Record<string, unknown>;
  reason: string;
  alternatives: string[];
  deadline: string;
  status: string;
  createdAt: string;
  updatedAt: string;
};

export type RuntimeRetrievalSummary = {
  retrievalRunId: string;
  ownerScope: string;
  workspaceId: string | null;
  taskId: string | null;
  solverId: string | null;
  intentId: string | null;
  indexSnapshotId: string;
  method: string;
  queryPreview: string;
  hitCount: number;
  createdAt: string;
};

export type RuntimeEvent = {
  schemaVersion: number;
  id: string;
  taskId: string;
  seq: number;
  type: string;
  solverId: string | null;
  intentId: string | null;
  payload: Record<string, unknown>;
  createdAt: string;
};

type EntitySequence = {
  solversById: Record<string, number>;
  intentsById: Record<string, number>;
  workerResultsById: Record<string, number>;
  knowledgeById: Record<string, number>;
  evidenceById: Record<string, number>;
  findingsById: Record<string, number>;
  approvalsById: Record<string, number>;
  retrievalById: Record<string, number>;
};

export type RuntimeStore = {
  schemaVersion: 6;
  task: RuntimeTask;
  taskCommonSkillSnapshot?: Record<string, unknown>;
  session: RuntimeSession;
  team: RuntimeTeam;
  solversById: Record<string, RuntimeSolver>;
  intentsById: Record<string, RuntimeIntent>;
  workerResultsById: Record<string, RuntimeWorkerResult>;
  knowledgeById: Record<string, RuntimeKnowledgeItem>;
  artifactsById: Record<string, RuntimeArtifact>;
  evidenceById: Record<string, RuntimeEvidenceClaim>;
  findingsById: Record<string, RuntimeFinding>;
  actionsById: Record<string, RuntimeAction>;
  approvalsById: Record<string, RuntimeApproval>;
  retrievalById: Record<string, RuntimeRetrievalSummary>;
  eventsBySeq: Record<number, RuntimeEvent>;
  globalPlan: RuntimeGlobalPlan | null;
  modeProjection: {
    challenge: Record<string, unknown>;
    flags: Array<Record<string, unknown>>;
    artifactIndexes: Array<Record<string, unknown>>;
  };
  latestSeq: number;
  eventHistoryHasMore: boolean;
  entitySequence: EntitySequence;
};

export type SolverTreeNode = { solver: RuntimeSolver; children: SolverTreeNode[] };
