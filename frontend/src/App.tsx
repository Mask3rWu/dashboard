import { useState, useEffect, Component, type ReactNode } from 'react';
import ImportPage from './pages/ImportPage';
import FlightView from './pages/FlightView';
import ComparePage from './pages/ComparePage';
import ModelManager from './pages/ModelManager';
import {
  checkHealth, listFlights, listModels, listAircraft,
  getAppContext, updateAppContext, login, logout, changePassword, createUser, setSessionToken,
  type Flight, type AircraftModel, type Aircraft, type AppContext,
} from './api';

type Tab = 'import' | 'models' | 'flight' | 'compare';
type Capability = AppContext['capabilities'][number];

// ═══════════════════════════════════════════════════════════════
// Error Boundary — prevents a single component error from
// crashing the entire app (white screen).
// ═══════════════════════════════════════════════════════════════
interface EBState { hasError: boolean; error: Error | null }
class ErrorBoundary extends Component<{ children: ReactNode; fallback?: ReactNode }, EBState> {
  state: EBState = { hasError: false, error: null };
  static getDerivedStateFromError(error: Error): EBState {
    return { hasError: true, error };
  }
  render() {
    if (this.state.hasError) {
      return this.props.fallback ?? (
        <div className="flex items-center justify-center h-full bg-white">
          <div className="text-center max-w-xl p-8">
            <div className="text-red-500 text-4xl mb-4">⚠️</div>
            <h2 className="text-lg font-bold text-gray-800 mb-2">页面发生了错误</h2>
            <p className="text-sm text-gray-500 mb-4">{this.state.error?.message}</p>
            <button
              onClick={() => this.setState({ hasError: false, error: null })}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-500"
            >
              重试
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

function hasCapability(context: AppContext | null, cap: Capability): boolean {
  return context?.capabilities.includes(cap) ?? false;
}

function AccountPanel({
  context,
  onContextChanged,
}: {
  context: AppContext;
  onContextChanged: (ctx: AppContext) => void;
}) {
  const [open, setOpen] = useState(false);
  const [loginName, setLoginName] = useState('admin');
  const [loginPassword, setLoginPassword] = useState('');
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newUsername, setNewUsername] = useState('');
  const [newUserPassword, setNewUserPassword] = useState('');
  const [newUserRole, setNewUserRole] = useState<'admin' | 'user'>('user');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  const canManageUsers = context.capabilities.includes('manage_users');

  const submitLogin = async () => {
    if (!loginName.trim() || !loginPassword) return;
    setBusy(true);
    setMessage('');
    try {
      const result = await login(loginName.trim(), loginPassword);
      setSessionToken(result.token);
      onContextChanged(result);
      setLoginPassword('');
      setOpen(false);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const submitLogout = async () => {
    setBusy(true);
    try {
      await logout();
    } catch {
      // Local logout should still clear the stored token.
    } finally {
      setSessionToken(null);
      onContextChanged(await getAppContext());
      setBusy(false);
      setOpen(false);
    }
  };

  const submitPasswordChange = async () => {
    if (!oldPassword || !newPassword) return;
    setBusy(true);
    setMessage('');
    try {
      await changePassword(oldPassword, newPassword);
      setSessionToken(null);
      setOldPassword('');
      setNewPassword('');
      onContextChanged(await getAppContext());
      setMessage('密码已修改，请重新登录');
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const submitCreateUser = async () => {
    if (!newUsername.trim() || !newUserPassword) return;
    setBusy(true);
    setMessage('');
    try {
      await createUser(newUsername.trim(), newUserPassword, newUserRole);
      setNewUsername('');
      setNewUserPassword('');
      setNewUserRole('user');
      setMessage('用户已创建');
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (context.environment === 'field') {
    return <span className="text-xs text-gray-400">外场离线模式</span>;
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => { setOpen((v) => !v); setMessage(''); }}
        className="px-2 py-1 text-xs border border-gray-300 rounded bg-white hover:bg-gray-50 text-gray-600"
      >
        {context.user ? `${context.user.username} (${context.user.role === 'admin' ? '管理员' : '用户'})` : '登录'}
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-2 w-80 bg-white border border-gray-200 rounded-lg shadow-lg z-50 p-4 space-y-4">
          {!context.user ? (
            <div className="space-y-2">
              <div className="text-sm font-medium text-gray-800">科研网登录</div>
              <input
                value={loginName}
                onChange={(e) => setLoginName(e.target.value)}
                className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
                placeholder="用户名"
              />
              <input
                type="password"
                value={loginPassword}
                onChange={(e) => setLoginPassword(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') submitLogin(); }}
                className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
                placeholder="密码"
              />
              <button
                type="button"
                disabled={busy}
                onClick={submitLogin}
                className="w-full px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-500 disabled:opacity-50"
              >
                登录
              </button>
            </div>
          ) : (
            <>
              <div className="space-y-2">
                <div className="text-sm font-medium text-gray-800">修改密码</div>
                <input
                  type="password"
                  value={oldPassword}
                  onChange={(e) => setOldPassword(e.target.value)}
                  className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
                  placeholder="旧密码"
                />
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
                  placeholder="新密码"
                />
                <button
                  type="button"
                  disabled={busy}
                  onClick={submitPasswordChange}
                  className="w-full px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-500 disabled:opacity-50"
                >
                  修改密码
                </button>
              </div>
              {canManageUsers && (
                <div className="space-y-2 border-t border-gray-100 pt-3">
                  <div className="text-sm font-medium text-gray-800">新建用户</div>
                  <input
                    value={newUsername}
                    onChange={(e) => setNewUsername(e.target.value)}
                    className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
                    placeholder="用户名"
                  />
                  <input
                    type="password"
                    value={newUserPassword}
                    onChange={(e) => setNewUserPassword(e.target.value)}
                    className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
                    placeholder="初始密码"
                  />
                  <select
                    value={newUserRole}
                    onChange={(e) => setNewUserRole(e.target.value as 'admin' | 'user')}
                    className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm bg-white"
                  >
                    <option value="user">普通用户</option>
                    <option value="admin">管理员</option>
                  </select>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={submitCreateUser}
                    className="w-full px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-500 disabled:opacity-50"
                  >
                    创建用户
                  </button>
                </div>
              )}
              <button
                type="button"
                disabled={busy}
                onClick={submitLogout}
                className="w-full px-3 py-1.5 text-sm bg-gray-100 text-gray-600 rounded hover:bg-gray-200 disabled:opacity-50"
              >
                退出登录
              </button>
            </>
          )}
          {message && <div className="text-xs text-red-500 bg-red-50 rounded px-2 py-1">{message}</div>}
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>('flight');
  const [flights, setFlights] = useState<Flight[]>([]);
  const [selectedFlightId, setSelectedFlightId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [initError, setInitError] = useState<string | null>(null);
  const [modelsVersion, setModelsVersion] = useState(0);
  const [appContext, setAppContextState] = useState<AppContext | null>(null);

  // ── Three-level selection: Model → Aircraft → Flight ──
  const [models, setModels] = useState<AircraftModel[]>([]);
  const [selectedModelId, setSelectedModelId] = useState<number | null>(null);
  const [aircraft, setAircraft] = useState<Aircraft[]>([]);
  const [selectedAircraftId, setSelectedAircraftId] = useState<number | null>(null);

  const loadFlights = async () => {
    try {
      const data = await listFlights();
      setFlights(data.flights);
    } catch (e) {
      console.error('Failed to load flights:', e);
    }
  };

  const loadModels = async () => {
    try {
      const data = await listModels();
      setModels(data.models);
      if (data.models.length > 0 && !selectedModelId) {
        setSelectedModelId(data.models[0].id);
      }
    } catch (e) {
      console.error('Failed to load models:', e);
    }
  };

  const loadAircraftForModel = async (modelId: number) => {
    try {
      const data = await listAircraft(modelId);
      setAircraft(data.aircraft);
      // Use functional update to avoid overwriting a user selection
      // that was set concurrently (e.g. from ComparePage tree selector).
      // Only auto-select the first aircraft if the current selection does
      // not belong to this model.
      setSelectedAircraftId((prev) => {
        if (prev != null && data.aircraft.some((a) => a.id === prev)) {
          return prev; // preserve user's explicit selection
        }
        return data.aircraft.length > 0 ? data.aircraft[0].id : null;
      });
    } catch (e) {
      console.error('Failed to load aircraft:', e);
      setAircraft([]);
      setSelectedAircraftId(null);
    }
  };

  const onDataChanged = () => {
    loadFlights();
    loadModels();
    setModelsVersion(v => v + 1);
  };

  const handleEnvironmentChange = async (environment: 'research' | 'field') => {
    const ctx = await updateAppContext({ environment });
    if (environment === 'field') {
      setSessionToken(null);
      const fresh = await getAppContext();
      setAppContextState(fresh);
    } else {
      setAppContextState(ctx);
    }
  };

  const doInit = async () => {
    setLoading(true);
    setInitError(null);
    try {
      await checkHealth();
      const [contextData, modelsData, flightsData] = await Promise.all([getAppContext(), listModels(), listFlights()]);
      setAppContextState(contextData);
      setModels(modelsData.models);
      setFlights(flightsData.flights);
      // Auto-select first model → first aircraft → first flight
      if (modelsData.models.length > 0) {
        const firstModelId = modelsData.models[0].id;
        setSelectedModelId(firstModelId);
        try {
          const acData = await listAircraft(firstModelId);
          setAircraft(acData.aircraft);
          if (acData.aircraft.length > 0) {
            const firstAcId = acData.aircraft[0].id;
            setSelectedAircraftId(firstAcId);
            const acFlights = flightsData.flights.filter(f => f.aircraft_id === firstAcId);
            if (acFlights.length > 0) {
              setSelectedFlightId(acFlights[0].id);
            }
          }
        } catch { /* aircraft load failed, ignore */ }
      }
    } catch (err) {
      console.error('Failed to initialize', err);
      const message = err instanceof Error ? err.message : String(err);
      setInitError(
        `${message}\n\n如问题持续，请查看日志：%APPDATA%\\FlightAnalyzer\\startup.log`
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { doInit(); }, []);

  // When model changes, load its aircraft
  useEffect(() => {
    if (selectedModelId) {
      loadAircraftForModel(selectedModelId);
    } else {
      setAircraft([]);
      setSelectedAircraftId(null);
    }
  }, [selectedModelId]);

  // When aircraft changes, auto-select first flight under that aircraft
  useEffect(() => {
    const acFlights = selectedAircraftId
      ? flights.filter(f => f.aircraft_id === selectedAircraftId)
      : selectedModelId
        ? flights.filter(f => f.model_id === selectedModelId)
        : flights;
    if (acFlights.length > 0) {
      const currentInList = acFlights.some(f => f.id === selectedFlightId);
      if (!currentInList) {
        setSelectedFlightId(acFlights[0].id);
      }
    } else {
      setSelectedFlightId(null);
    }
  }, [selectedAircraftId, selectedModelId, flights]);

  const navigateToFlight = (flightId: number) => {
    setSelectedFlightId(flightId);
    setTab('flight');
  };

  const tabs: { key: Tab; label: string }[] = [
    { key: 'import', label: '导入数据' },
    { key: 'models', label: '数据管理' },
    { key: 'flight', label: '飞行分析' },
    { key: 'compare', label: '飞行对比' },
  ];

  return (
    <div className="h-screen flex flex-col bg-white text-gray-900">
      <header className="flex items-center justify-between shrink-0 border-b border-gray-200 px-6 h-14 bg-gray-50">
        <div className="flex items-center gap-6">
          <h1 className="text-lg font-bold text-blue-600 tracking-wide">Flight Analyzer</h1>
          <nav className="flex gap-1">
            {tabs.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                  tab === t.key
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-3">
          {appContext && (
            <>
              <select
                value={appContext.environment}
                onChange={(e) => handleEnvironmentChange(e.target.value as 'research' | 'field')}
                className="bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-600"
                title="环境模式"
              >
                <option value="research">科研网</option>
                <option value="field">外场</option>
              </select>
              <span className="text-xs text-gray-400" title={appContext.node_id}>
                {appContext.node_id}
              </span>
              <AccountPanel context={appContext} onContextChanged={setAppContextState} />
            </>
          )}
          {flights.length > 0 && (
            <span className="text-xs text-gray-400">{flights.length} 架次已导入</span>
          )}
        </div>
      </header>
      <main className="flex-1 overflow-hidden relative">
        {loading ? (
          <div className="flex items-center justify-center h-full text-gray-400">加载中...</div>
        ) : initError ? (
          <div className="flex items-center justify-center h-full">
            <div className="text-center max-w-md p-8">
              <div className="text-red-500 text-4xl mb-4">⚠️</div>
              <h2 className="text-lg font-bold text-gray-800 mb-2">连接失败</h2>
              <p className="text-sm text-gray-500 mb-4 whitespace-pre-line">{initError}</p>
              <button
                onClick={doInit}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-500"
              >
                重试
              </button>
            </div>
          </div>
        ) : (
          <>
            {/* Use visibility + absolute positioning for keep-alive instead of
                display:contents which breaks CSS layout (h-full, flex children)
                and causes ECharts container dimension failures in WebView2.
                Only the active tab is in-flow; hidden tabs are positioned off-screen
                so they stay mounted (preserving state) but don't affect layout. */}
            <div className={tab === 'import' ? 'h-full' : 'invisible absolute inset-0 overflow-hidden'}>
              <ErrorBoundary>
                <ImportPage
                  onImported={onDataChanged}
                  canDeleteFlights={hasCapability(appContext, 'delete_flights')}
                />
              </ErrorBoundary>
            </div>
            <div className={tab === 'flight' ? 'h-full' : 'invisible absolute inset-0 overflow-hidden'}>
              <ErrorBoundary>
                <FlightView
                  active={tab === 'flight'}
                  flights={flights}
                  selectedFlightId={selectedFlightId}
                  onSelectFlight={setSelectedFlightId}
                  onFlightsChanged={onDataChanged}
                  models={models}
                  selectedModelId={selectedModelId}
                  onSelectModel={setSelectedModelId}
                  aircraft={aircraft}
                  selectedAircraftId={selectedAircraftId}
                  onSelectAircraft={setSelectedAircraftId}
                  canDeleteFlights={hasCapability(appContext, 'delete_flights')}
                />
              </ErrorBoundary>
            </div>
            <div className={tab === 'compare' ? 'h-full' : 'invisible absolute inset-0 overflow-hidden'}>
              <ErrorBoundary>
                <ComparePage
                  flights={flights}
                  models={models}
                  selectedModelId={selectedModelId}
                  onSelectModel={setSelectedModelId}
                  aircraft={aircraft}
                  selectedAircraftId={selectedAircraftId}
                  onSelectAircraft={setSelectedAircraftId}
                />
              </ErrorBoundary>
            </div>
            <div className={tab === 'models' ? 'h-full' : 'invisible absolute inset-0 overflow-hidden'}>
              <ErrorBoundary>
                <ModelManager
                  onModelsChanged={onDataChanged}
                  onNavigateToFlight={navigateToFlight}
                  flights={flights}
                  modelsVersion={modelsVersion}
                  capabilities={appContext?.capabilities ?? []}
                />
              </ErrorBoundary>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
