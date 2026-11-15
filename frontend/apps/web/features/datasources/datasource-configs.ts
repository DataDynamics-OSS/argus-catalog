// ---------------------------------------------------------------------------
// 어떤 데이터 소스 type 이 "지원" 되는지 (메타데이터 sync 어댑터 또는 source-analyzer 익스텐션이
// 실제로 존재하는지) 의 단일 출처. UI 의 "데이터 소스 추가" 다이얼로그, 데이터셋 목록의 데이터 소스
// 필터 등 여러 곳에서 공유한다.
// ---------------------------------------------------------------------------

// ``hiddenFromAddDialog`` 는 "데이터 소스 추가" 다이얼로그의 타입 선택 그리드에서만 숨긴다.
// 어댑터/익스텐션은 구현돼 있지만 사용자가 UI 에서 직접 설정·운영할 방법이 없는 타입
// (HDFS = 파일시스템 스캔, Java/Python Source = 빌드 산출물 분석)을 가린다. 기존
// 데이터셋의 datasource.type 표시·필터링은 그대로 동작해야 하므로 DATASOURCE_TYPES 자체
// 에서 제거하지는 않는다.
// Amazon S3 · Google BigQuery · MongoDB 는 지원 계획이 없어 추가 목록에서 뺐다(2026-11-15 사용자 요청).
// 데이터 소스 역할 (design/catalog-entity-design.md 2단계) — 백엔드 datasource_metadata.DATASOURCE_ROLES 와 맞춘다.
// CATALOG: 메타데이터 원본(카탈로그 서비스) · DATABASE: DB 자체가 카탈로그 · ENGINE: 쿼리 엔진(카탈로그에 연결)
export type DatasourceRole = "CATALOG" | "DATABASE" | "ENGINE"

export const DATASOURCE_ROLE_LABELS: Record<DatasourceRole, { title: string; desc: string }> = {
  CATALOG: { title: "카탈로그", desc: "테이블 메타데이터를 가진 카탈로그 서비스 — 메타데이터를 수집합니다." },
  DATABASE: { title: "데이터베이스", desc: "데이터베이스 자체가 카탈로그입니다 — 메타데이터를 수집합니다." },
  ENGINE: { title: "쿼리 엔진", desc: "자체 카탈로그가 없습니다 — 등록된 카탈로그에 연결해 쿼리 이력·리니지를 수집합니다." },
}

export const DATASOURCE_TYPES: {
  name: string
  display: string
  implemented: boolean
  role: DatasourceRole
  hiddenFromAddDialog?: boolean
}[] = [
  { name: "postgresql", display: "PostgreSQL", role: "DATABASE", implemented: true },
  { name: "mysql", display: "MySQL", role: "DATABASE", implemented: true },
  { name: "oracle", display: "Oracle", role: "DATABASE", implemented: true },
  { name: "mssql", display: "Microsoft SQL Server", role: "DATABASE", implemented: true },
  { name: "hive", display: "Apache Hive", role: "CATALOG", implemented: true },
  { name: "impala", display: "Apache Impala", role: "ENGINE", implemented: true },
  { name: "kafka", display: "Apache Kafka", role: "DATABASE", implemented: false },
  { name: "s3", display: "Amazon S3", role: "DATABASE", implemented: false, hiddenFromAddDialog: true },
  { name: "hdfs", display: "HDFS", role: "DATABASE", implemented: false, hiddenFromAddDialog: true },
  { name: "snowflake", display: "Snowflake", role: "DATABASE", implemented: false },
  { name: "bigquery", display: "Google BigQuery", role: "DATABASE", implemented: false, hiddenFromAddDialog: true },
  { name: "redshift", display: "Amazon Redshift", role: "DATABASE", implemented: false },
  { name: "elasticsearch", display: "Elasticsearch", role: "DATABASE", implemented: false },
  { name: "mongodb", display: "MongoDB", role: "DATABASE", implemented: false, hiddenFromAddDialog: true },
  { name: "trino", display: "Trino", role: "ENGINE", implemented: true },
  { name: "starrocks", display: "StarRocks", role: "DATABASE", implemented: true },
  { name: "greenplum", display: "Greenplum", role: "DATABASE", implemented: true },
  { name: "kudu", display: "Apache Kudu", role: "CATALOG", implemented: true },
  { name: "iceberg-rest", display: "Iceberg REST Catalog", role: "CATALOG", implemented: true },
  { name: "iceberg-hadoop", display: "Iceberg Hadoop Catalog", role: "CATALOG", implemented: true },
  { name: "unity_catalog", display: "Unity Catalog", role: "CATALOG", implemented: false },
  { name: "java", display: "Java Source", role: "DATABASE", implemented: true, hiddenFromAddDialog: true },
  { name: "python", display: "Python Source", role: "DATABASE", implemented: true, hiddenFromAddDialog: true },
]

export const IMPLEMENTED_DATASOURCE_TYPES: ReadonlySet<string> = new Set(
  DATASOURCE_TYPES.filter((p) => p.implemented).map((p) => p.name),
)

export function datasourceRole(type: string | null | undefined): DatasourceRole {
  return DATASOURCE_TYPES.find((t) => t.name === type)?.role ?? "DATABASE"
}

export function isDatasourceTypeImplemented(type: string | null | undefined): boolean {
  if (!type) return false
  return IMPLEMENTED_DATASOURCE_TYPES.has(type)
}


export type DatasourceFieldDef = {
  key: string
  label: string
  /** list: 값 목록 — 배지 입력(TODO-144), 문자열 배열로 저장(예전 쉼표 문자열도 읽음) */
  type: 'text' | 'number' | 'password' | 'select' | 'toggle' | 'textarea' | 'list'
  placeholder?: string
  required?: boolean
  defaultValue?: string | number | boolean
  options?: { label: string; value: string }[]
  showWhen?: { field: string; value: string }
}

// ---------------------------------------------------------------------------
// Shared field fragments
// ---------------------------------------------------------------------------

const hostField = (defaultPort: number): DatasourceFieldDef[] => [
  { key: 'host', label: 'Host', type: 'text', required: true },
  { key: 'port', label: 'Port', type: 'number', required: true, defaultValue: defaultPort },
]

const dbCredentials: DatasourceFieldDef[] = [
  { key: 'database', label: 'Database', type: 'text', required: true },
  { key: 'username', label: 'Username', type: 'text', required: true },
  { key: 'password', label: 'Password', type: 'password', required: true },
]

const sslToggle = (defaultValue = false): DatasourceFieldDef => ({
  key: 'ssl_enabled',
  label: 'SSL Enabled',
  type: 'toggle',
  defaultValue,
})

const kerberosFields: DatasourceFieldDef[] = [
  { key: 'kerberos_principal', label: 'Kerberos Principal', type: 'text', showWhen: { field: 'auth_type', value: 'KERBEROS' } },
  { key: 'kerberos_keytab', label: 'Kerberos Keytab', type: 'text', placeholder: '/path/to/keytab', showWhen: { field: 'auth_type', value: 'KERBEROS' } },
]

const ldapFields: DatasourceFieldDef[] = [
  { key: 'ldap_username', label: 'LDAP Username', type: 'text', showWhen: { field: 'auth_type', value: 'LDAP' } },
  { key: 'ldap_password', label: 'LDAP Password', type: 'password', showWhen: { field: 'auth_type', value: 'LDAP' } },
]

const hadoopAuthType = (options: string[]): DatasourceFieldDef => ({
  key: 'auth_type',
  label: 'Authentication',
  type: 'select',
  required: true,
  defaultValue: 'NONE',
  options: options.map((v) => ({ label: v, value: v })),
})

// ---------------------------------------------------------------------------
// Helpers for RDBMS-like datasources
// ---------------------------------------------------------------------------

function rdbmsConfig(defaultPort: number): DatasourceFieldDef[] {
  return [...hostField(defaultPort), ...dbCredentials, sslToggle()]
}

// ---------------------------------------------------------------------------
// Datasource configurations
// ---------------------------------------------------------------------------

export const DATASOURCE_CONFIGS: Record<string, DatasourceFieldDef[]> = {
  // ---- RDBMS-like --------------------------------------------------------
  postgresql: rdbmsConfig(5432),
  mysql: rdbmsConfig(3306),
  greenplum: rdbmsConfig(5432),
  redshift: rdbmsConfig(5439),
  starrocks: rdbmsConfig(9030),

  // ---- Oracle ------------------------------------------------------------
  // 수집은 metadata-sync(oracledb thin) — Service Name 이 있으면 그것을, 없으면 SID 로 접속한다.
  oracle: [
    ...hostField(1521),
    { key: 'service_name', label: 'Service Name', type: 'text', placeholder: '예: ORCLPDB1 (SID 로 접속하면 비워 둠)' },
    { key: 'sid', label: 'SID', type: 'text', placeholder: 'Service Name 이 없을 때만' },
    { key: 'username', label: 'Username', type: 'text', required: true },
    { key: 'password', label: 'Password', type: 'password', required: true },
  ],

  // ---- Microsoft SQL Server ----------------------------------------------
  // Database 는 처음 접속할 DB 다(비우면 master). 수집할 데이터베이스는 수집 범위에서 고른다.
  mssql: [
    ...hostField(1433),
    { key: 'database', label: 'Database (접속용)', type: 'text', placeholder: '비우면 master' },
    { key: 'username', label: 'Username', type: 'text', required: true },
    { key: 'password', label: 'Password', type: 'password', required: true },
  ],

  // ---- Snowflake ---------------------------------------------------------
  snowflake: [
    { key: 'account', label: 'Account', type: 'text', required: true, placeholder: 'org-account' },
    { key: 'warehouse', label: 'Warehouse', type: 'text' },
    { key: 'database', label: 'Database', type: 'text', required: true },
    { key: 'schema', label: 'Schema', type: 'text', defaultValue: 'PUBLIC' },
    { key: 'username', label: 'Username', type: 'text', required: true },
    { key: 'password', label: 'Password', type: 'password', required: true },
    { key: 'role', label: 'Role', type: 'text' },
  ],

  // ---- BigQuery ----------------------------------------------------------
  bigquery: [
    { key: 'project_id', label: 'Project ID', type: 'text', required: true },
    { key: 'dataset', label: 'Dataset', type: 'text' },
    { key: 'credentials_json', label: 'Credentials JSON', type: 'text', placeholder: 'Paste service account JSON' },
  ],

  // ---- Hive --------------------------------------------------------------
  hive: [
    { key: 'metastore_host', label: 'Metastore Host', type: 'text', required: true },
    { key: 'metastore_port', label: 'Metastore Port', type: 'number', required: true, defaultValue: 9083 },
    hadoopAuthType(['NONE', 'LDAP', 'KERBEROS']),
    ...ldapFields,
    ...kerberosFields,
  ],

  // ---- Impala ------------------------------------------------------------
  impala: [
    ...hostField(21050),
    hadoopAuthType(['NONE', 'LDAP', 'KERBEROS']),
    ...ldapFields,
    ...kerberosFields,
    { key: 'use_ssl', label: 'Use SSL', type: 'toggle', defaultValue: false },
  ],

  // ---- Trino -------------------------------------------------------------
  trino: [
    ...hostField(8443),
    // 엔진은 여러 카탈로그에 연결된다(카탈로그 연결에서 관리). 이 값은 엔진 경유 수집 시 기본 카탈로그로만 쓴다.
    { key: 'catalog', label: 'Catalog (엔진 경유 수집 시)', type: 'text' },
    { key: 'schema', label: 'Schema', type: 'text' },
    { key: 'username', label: 'Username', type: 'text', required: true },
    { key: 'password', label: 'Password', type: 'password' },
    { key: 'use_ssl', label: 'Use SSL', type: 'toggle', defaultValue: true },
  ],

  // ---- Kafka -------------------------------------------------------------
  kafka: [
    { key: 'bootstrap_servers', label: 'Bootstrap Servers', type: 'text', required: true, placeholder: 'host1:9092,host2:9092' },
    {
      key: 'security_protocol',
      label: 'Security Protocol',
      type: 'select',
      defaultValue: 'PLAINTEXT',
      options: [
        { label: 'PLAINTEXT', value: 'PLAINTEXT' },
        { label: 'SSL', value: 'SSL' },
        { label: 'SASL_PLAINTEXT', value: 'SASL_PLAINTEXT' },
        { label: 'SASL_SSL', value: 'SASL_SSL' },
      ],
    },
    { key: 'sasl_mechanism', label: 'SASL Mechanism', type: 'text', showWhen: { field: 'security_protocol', value: 'SASL_PLAINTEXT' } },
    { key: 'sasl_username', label: 'SASL Username', type: 'text', showWhen: { field: 'security_protocol', value: 'SASL_PLAINTEXT' } },
    { key: 'sasl_password', label: 'SASL Password', type: 'password', showWhen: { field: 'security_protocol', value: 'SASL_PLAINTEXT' } },
    // Duplicate entries for SASL_SSL so the fields also appear for that protocol
    { key: 'sasl_mechanism', label: 'SASL Mechanism', type: 'text', showWhen: { field: 'security_protocol', value: 'SASL_SSL' } },
    { key: 'sasl_username', label: 'SASL Username', type: 'text', showWhen: { field: 'security_protocol', value: 'SASL_SSL' } },
    { key: 'sasl_password', label: 'SASL Password', type: 'password', showWhen: { field: 'security_protocol', value: 'SASL_SSL' } },
    { key: 'schema_registry_url', label: 'Schema Registry URL', type: 'text', placeholder: 'http://schema-registry:8081' },
  ],

  // ---- Elasticsearch -----------------------------------------------------
  elasticsearch: [
    { key: 'hosts', label: 'Hosts', type: 'text', required: true, placeholder: 'http://localhost:9200' },
    { key: 'username', label: 'Username', type: 'text' },
    { key: 'password', label: 'Password', type: 'password' },
    { key: 'use_ssl', label: 'Use SSL', type: 'toggle', defaultValue: false },
    { key: 'ca_cert_path', label: 'CA Certificate Path', type: 'text', placeholder: '/path/to/ca.crt' },
  ],

  // ---- MongoDB -----------------------------------------------------------
  mongodb: [
    { key: 'connection_string', label: 'Connection String', type: 'text', required: true, placeholder: 'mongodb://host:27017' },
    { key: 'database', label: 'Database', type: 'text', required: true },
    { key: 'username', label: 'Username', type: 'text' },
    { key: 'password', label: 'Password', type: 'password' },
    { key: 'auth_source', label: 'Auth Source', type: 'text', defaultValue: 'admin' },
  ],

  // ---- S3 ----------------------------------------------------------------
  s3: [
    { key: 'endpoint_url', label: 'Endpoint URL', type: 'text', placeholder: 'https://s3.amazonaws.com' },
    { key: 'region', label: 'Region', type: 'text', required: true, defaultValue: 'us-east-1' },
    { key: 'access_key_id', label: 'Access Key ID', type: 'text', required: true },
    { key: 'secret_access_key', label: 'Secret Access Key', type: 'password', required: true },
    { key: 'bucket', label: 'Bucket', type: 'text' },
  ],

  // ---- HDFS --------------------------------------------------------------
  hdfs: [
    { key: 'namenode_host', label: 'NameNode Host', type: 'text', required: true },
    { key: 'namenode_port', label: 'NameNode Port', type: 'number', required: true, defaultValue: 8020 },
    hadoopAuthType(['NONE', 'KERBEROS']),
    ...kerberosFields,
  ],

  // ---- Kudu --------------------------------------------------------------
  kudu: [
    { key: 'master_addresses', label: 'Master Addresses', type: 'text', required: true, placeholder: 'host1:7051,host2:7051' },
    hadoopAuthType(['NONE', 'KERBEROS']),
    ...kerberosFields,
  ],

  // ---- Java Source -------------------------------------------------------
  java: [
    { key: 'source_directory', label: 'Source Directory', type: 'text', required: true, placeholder: '/path/to/java/project/src' },
    { key: 'project_name', label: 'Project Name', type: 'text', required: true },
    {
      key: 'framework',
      label: 'Framework',
      type: 'select',
      required: true,
      defaultValue: 'ALL',
      options: [
        { label: 'All Frameworks', value: 'ALL' },
        { label: 'JPA / Hibernate', value: 'JPA' },
        { label: 'MyBatis', value: 'MYBATIS' },
        { label: 'Spring JDBC / JDBC', value: 'JDBC' },
      ],
    },
    { key: 'java_version', label: 'Java Version', type: 'text', placeholder: '17 (auto-detected from pom.xml)' },
    { key: 'exclude_dirs', label: 'Exclude Directories', type: 'list', placeholder: '예: target 입력 후 Enter' },
  ],

  // ---- Python Source ----------------------------------------------------
  python: [
    { key: 'source_directory', label: 'Source Directory', type: 'text', required: true, placeholder: '/path/to/python/project/src' },
    { key: 'project_name', label: 'Project Name', type: 'text', required: true },
    {
      key: 'framework',
      label: 'Framework',
      type: 'select',
      required: true,
      defaultValue: 'ALL',
      options: [
        { label: 'All Frameworks', value: 'ALL' },
        { label: 'SQLAlchemy', value: 'SQLALCHEMY' },
        { label: 'Django ORM', value: 'DJANGO' },
        { label: 'DB-API (Raw SQL)', value: 'DBAPI' },
      ],
    },
    { key: 'python_version', label: 'Python Version', type: 'text', placeholder: '3.11 (auto-detected from pyproject.toml)' },
    { key: 'exclude_dirs', label: 'Exclude Directories', type: 'list', placeholder: '예: .venv 입력 후 Enter' },
  ],

  // ---- Iceberg REST Catalog ---------------------------------------------
  // Apache Polaris / Project Nessie / Lakekeeper / Tabular 등 REST Catalog
  // 스펙 구현체 공통 설정. 백엔드 catalog_datasources.type='iceberg-rest' 와 매칭.
  "iceberg-rest": [
    { key: 'uri', label: 'Catalog URI', type: 'text', required: true, placeholder: 'http://polaris:8181/api/catalog' },
    { key: 'warehouse', label: 'Warehouse', type: 'text', placeholder: '다중 카탈로그(Polaris)일 때 카탈로그 이름' },
    {
      key: 'auth_type',
      label: 'Authentication',
      type: 'select',
      required: true,
      defaultValue: 'NONE',
      options: [
        { label: 'None', value: 'NONE' },
        { label: 'OAuth2 (client_credentials)', value: 'OAUTH2' },
        { label: 'Bearer Token', value: 'BEARER' },
      ],
    },
    { key: 'credential', label: 'OAuth2 Credential', type: 'password', placeholder: 'client_id:client_secret', showWhen: { field: 'auth_type', value: 'OAUTH2' } },
    { key: 'scope', label: 'OAuth2 Scope', type: 'text', defaultValue: 'PRINCIPAL_ROLE:ALL', showWhen: { field: 'auth_type', value: 'OAUTH2' } },
    { key: 'token', label: 'Bearer Token', type: 'password', showWhen: { field: 'auth_type', value: 'BEARER' } },
    // 수집 대상 네임스페이스는 상세 화면 '수집' 범위에서 고른다
    // 스토리지 자격증명은 비밀값으로 따로 받는다(암호화·마스킹) — Catalog Properties 에 넣으면 평문으로 저장·표시된다
    { key: 's3_access_key_id', label: 'S3 Access Key ID', type: 'text', placeholder: '카탈로그가 자격증명을 내려주지 않을 때(선택)' },
    { key: 's3_secret_access_key', label: 'S3 Secret Access Key', type: 'password' },
    {
      key: 'properties',
      label: 'Catalog Properties',
      type: 'textarea',
      placeholder: 's3.endpoint=http://minio:9000\ns3.path-style-access=true\n(한 줄에 하나)',
    },
    { key: 'exclude_namespaces', label: 'Exclude Namespaces', type: 'list', placeholder: '예: system 입력 후 Enter' },
    { key: 'origin', label: 'Origin', type: 'text', defaultValue: 'PROD' },
  ],

  // ---- Iceberg Hadoop Catalog -------------------------------------------
  // 메타스토어 없이 웨어하우스 디렉터리(<warehouse>/<ns>/<table>/metadata/version-hint.text)를 직접 읽는다.
  // 웨어하우스 스킴으로 저장소를 고른다 — s3:// (S3·MinIO), hdfs:// (WebHDFS), file:// (로컬·NFS).
  "iceberg-hadoop": [
    { key: 'warehouse', label: 'Warehouse', type: 'text', required: true, placeholder: 's3://bucket/warehouse 또는 hdfs://namenode:8020/warehouse' },
    { key: 's3_endpoint', label: 'S3 Endpoint', type: 'text', placeholder: 'http://minio:9000 (AWS S3 면 비움)' },
    { key: 's3_region', label: 'S3 Region', type: 'text', placeholder: 'us-east-1 (선택)' },
    { key: 's3_access_key_id', label: 'S3 Access Key ID', type: 'text', placeholder: '비우면 실행 환경의 AWS 자격증명' },
    { key: 's3_secret_access_key', label: 'S3 Secret Access Key', type: 'password' },
    { key: 'webhdfs_url', label: 'WebHDFS URL', type: 'text', placeholder: 'http://namenode:9870 (hdfs:// 웨어하우스일 때)' },
    hadoopAuthType(['NONE', 'KERBEROS']),
    { key: 'hdfs_user', label: 'HDFS User', type: 'text', placeholder: 'WebHDFS user.name (선택)', showWhen: { field: 'auth_type', value: 'NONE' } },
    ...kerberosFields,
    { key: 'exclude_namespaces', label: 'Exclude Namespaces', type: 'list', placeholder: '예: tmp 입력 후 Enter' },
    { key: 'origin', label: 'Origin', type: 'text', defaultValue: 'PROD' },
  ],

  // ---- Unity Catalog -----------------------------------------------------
  unity_catalog: [
    { key: 'api_url', label: 'API URL', type: 'text', required: true, placeholder: 'http://localhost:8080/api/2.1/unity-catalog' },
    {
      key: 'auth_type',
      label: 'Authentication',
      type: 'select',
      required: true,
      defaultValue: 'NONE',
      options: [
        { label: 'None', value: 'NONE' },
        { label: 'Token', value: 'TOKEN' },
        { label: 'OAuth', value: 'OAUTH' },
      ],
    },
    { key: 'token', label: 'Token', type: 'password', placeholder: 'Personal access token', showWhen: { field: 'auth_type', value: 'TOKEN' } },
    { key: 'oauth_client_id', label: 'OAuth Client ID', type: 'text', showWhen: { field: 'auth_type', value: 'OAUTH' } },
    { key: 'oauth_client_secret', label: 'OAuth Client Secret', type: 'password', showWhen: { field: 'auth_type', value: 'OAUTH' } },
  ],
}

// ---------------------------------------------------------------------------
// Utility: build a default-values map for a given datasource
// ---------------------------------------------------------------------------

export function getDefaultConfig(datasourceName: string): Record<string, unknown> {
  const fields = DATASOURCE_CONFIGS[datasourceName]
  if (!fields) return {}

  const defaults: Record<string, unknown> = {}
  const seen = new Set<string>()

  for (const field of fields) {
    // Skip duplicates (e.g. kafka SASL fields duplicated for two protocols)
    if (seen.has(field.key)) continue
    seen.add(field.key)

    if (field.defaultValue !== undefined) {
      defaults[field.key] = field.defaultValue
    }
  }

  return defaults
}
