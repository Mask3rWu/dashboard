import { useState, useEffect, Component, type ReactNode } from 'react';
import ImportPage from './pages/ImportPage';
import FlightView from './pages/FlightView';
import ComparePage from './pages/ComparePage';
import ModelManager from './pages/ModelManager';
import SyncPage from './pages/SyncPage';
import UserManagementPage from './pages/UserManagementPage';
import { checkHealth, getRuntimeContext, getAppContext, type Capability, type RuntimeContext, type AppContext } from './api/auth';
import { listFlights, type Flight } from './api/flights';
import { listModels, listAircraft, type AircraftModel, type Aircraft } from './api/models';
import RuntimeStatus from './components/RuntimeStatus';
import AccountMenu from './components/AccountMenu';

type Tab = 'import' | 'models' | 'flight' | 'compare' | 'sync' | 'users';

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
            <div className="text-red-500 text-4xl mb-4">!</div>
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

export default function App() {
  const [tab, setTab] = useState<Tab>('flight');
  const [flights, setFlights] = useState<Flight[]>([]);
  const [selectedFlightId, setSelectedFlightId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [initError, setInitError] = useState<string | null>(null);
  const [modelsVersion, setModelsVersion] = useState(0);
  const [runtimeContext, setRuntimeContext] = useState<RuntimeContext | null>(null);
  const [appContext, setAppContext] = useState<AppContext | null>(null);

  const mergedCapabilities = Array.from(new Set([
    ...(appContext?.capabilities ?? []),
    ...(runtimeContext?.server_capabilities ?? []),
  ]));
  const hasCapability = (cap: Capability) => mergedCapabilities.includes(cap);
  const serverOnline = !!runtimeContext?.server_reachable && !!runtimeContext?.server_user;
  const canManageServerUsers =
    serverOnline && (runtimeContext?.server_capabilities ?? []).includes('manage_users');
  const activeTab: Tab = tab;

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

  const loadAppContext = async () => {
    try {
      setAppContext(await getAppContext());
    } catch (e) {
      console.error('Failed to load app context:', e);
    }
  };

  const loadRuntimeContext = async () => {
    try {
      setRuntimeContext(await getRuntimeContext());
    } catch (e) {
      console.error('Failed to load runtime context:', e);
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
      setSelectedAircraftId((prev) => {
        if (prev != null && data.aircraft.some((a) => a.id === prev)) {
          return prev;
        }
        return data.aircraft.length > 0 ? data.aircraft[0].id : null;
      });
    } catch (e) {
      console.error('Failed to load aircraft:', e);
      setAircraft([]);
      setSelectedAircraftId(null);
    }
  };

  const onDataChanged = async () => {
    await Promise.all([
      loadFlights(),
      loadModels(),
      loadAppContext(),
      loadRuntimeContext(),
      selectedModelId ? loadAircraftForModel(selectedModelId) : Promise.resolve(),
    ]);
    setModelsVersion(v => v + 1);
  };

  const doInit = async () => {
    setLoading(true);
    setInitError(null);
    try {
      await checkHealth();
      const [appData, runtimeData, modelsData, flightsData] = await Promise.all([
        getAppContext(),
        getRuntimeContext(),
        listModels(),
        listFlights(),
      ]);
      setAppContext(appData);
      setRuntimeContext(runtimeData);
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
            const acFlights = flightsData.flights.filter(f => f.aircraft_id === firstAcId && f.sync_state !== 'server_deleted');
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

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    doInit();
  }, []);

  useEffect(() => {
    if (tab === 'users' && !canManageServerUsers) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setTab('flight');
    }
  }, [tab, canManageServerUsers]);

  // When model changes, load its aircraft
  useEffect(() => {
    if (selectedModelId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      loadAircraftForModel(selectedModelId);
    } else {
      setAircraft([]);
      setSelectedAircraftId(null);
    }
  }, [selectedModelId]);

  // When aircraft changes, auto-select first flight under that aircraft
  useEffect(() => {
    const availableFlights = flights.filter((f) => f.sync_state !== 'server_deleted');
    const acFlights = selectedAircraftId
      ? availableFlights.filter(f => f.aircraft_id === selectedAircraftId)
      : selectedModelId
        ? availableFlights.filter(f => f.model_id === selectedModelId)
        : availableFlights;
    if (acFlights.length > 0) {
      const currentInList = acFlights.some(f => f.id === selectedFlightId);
      if (!currentInList) {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setSelectedFlightId(acFlights[0].id);
      }
    } else {
      setSelectedFlightId(null);
    }
  }, [selectedAircraftId, selectedModelId, selectedFlightId, flights]);

  const navigateToFlight = (flightId: number) => {
    setSelectedFlightId(flightId);
    setTab('flight');
  };

  const visibleAnalysisFlights = flights.filter((f) => f.sync_state !== 'server_deleted');

  const tabs: { key: Tab; label: string }[] = [
    { key: 'import', label: '导入数据' },
    { key: 'models', label: '数据管理' },
    { key: 'flight', label: '飞行分析' },
    { key: 'compare', label: '飞行对比' },
    { key: 'sync', label: '同步队列' },
    ...(canManageServerUsers ? [{ key: 'users' as const, label: '用户管理' }] : []),
  ];

  return (
    <div className="h-screen flex flex-col bg-white text-gray-900">
      <header className="flex items-center justify-between shrink-0 border-b border-gray-200 px-6 h-14 bg-gray-50">
        <div className="flex items-center gap-6 min-w-0">
          <h1 className="text-lg font-bold text-blue-600 tracking-wide shrink-0">Flight Analyzer</h1>
          <nav className="flex gap-1 min-w-0 overflow-x-auto">
            {tabs.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors whitespace-nowrap ${
                  activeTab === t.key
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <RuntimeStatus runtime={runtimeContext} onOpenSync={() => setTab('sync')} />
          <AccountMenu
            context={appContext}
            onContextChanged={setAppContext}
            onAuthChanged={loadRuntimeContext}
          />
        </div>
      </header>
      <main className="flex-1 overflow-hidden relative">
        {loading ? (
          <div className="flex items-center justify-center h-full text-gray-400">加载中...</div>
        ) : initError ? (
          <div className="flex items-center justify-center h-full">
            <div className="text-center max-w-md p-8">
              <div className="text-red-500 text-4xl mb-4">!</div>
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
            <div className={activeTab === 'import' ? 'h-full' : 'invisible absolute inset-0 overflow-hidden'}>
              <ErrorBoundary>
                <ImportPage
                  onImported={onDataChanged}
                  canDeleteFlights={hasCapability('delete_flights')}
                  serverOnline={serverOnline}
                />
              </ErrorBoundary>
            </div>
            <div className={activeTab === 'flight' ? 'h-full' : 'invisible absolute inset-0 overflow-hidden'}>
              <ErrorBoundary>
                <FlightView
                  active={activeTab === 'flight'}
                  flights={visibleAnalysisFlights}
                  selectedFlightId={selectedFlightId}
                  onSelectFlight={setSelectedFlightId}
                  onFlightsChanged={onDataChanged}
                  models={models}
                  selectedModelId={selectedModelId}
                  onSelectModel={setSelectedModelId}
                  aircraft={aircraft}
                  selectedAircraftId={selectedAircraftId}
                  onSelectAircraft={setSelectedAircraftId}
                  canDeleteFlights={hasCapability('delete_flights')}
                  canEditColumns={hasCapability('update_columns')}
                  serverOnline={serverOnline}
                />
              </ErrorBoundary>
            </div>
            <div className={activeTab === 'compare' ? 'h-full' : 'invisible absolute inset-0 overflow-hidden'}>
              <ErrorBoundary>
                <ComparePage
                  flights={visibleAnalysisFlights}
                  models={models}
                  selectedModelId={selectedModelId}
                  onSelectModel={setSelectedModelId}
                  aircraft={aircraft}
                  selectedAircraftId={selectedAircraftId}
                  onSelectAircraft={setSelectedAircraftId}
                />
              </ErrorBoundary>
            </div>
            <div className={activeTab === 'models' ? 'h-full' : 'invisible absolute inset-0 overflow-hidden'}>
              <ErrorBoundary>
                <ModelManager
                  onModelsChanged={onDataChanged}
                  onNavigateToFlight={navigateToFlight}
                  flights={flights}
                  modelsVersion={modelsVersion}
                  capabilities={mergedCapabilities}
                  serverOnline={serverOnline}
                  isLoggedIn={!!appContext?.user || !!runtimeContext?.server_user}
                />
              </ErrorBoundary>
            </div>
            <div className={activeTab === 'sync' ? 'h-full' : 'invisible absolute inset-0 overflow-hidden'}>
              <ErrorBoundary>
                <SyncPage
                  runtime={runtimeContext}
                  onRefreshContext={loadRuntimeContext}
                  onDataChanged={onDataChanged}
                  onNavigateToFlight={navigateToFlight}
                />
              </ErrorBoundary>
            </div>
            {canManageServerUsers && (
              <div className={activeTab === 'users' ? 'h-full' : 'invisible absolute inset-0 overflow-hidden'}>
                <ErrorBoundary>
                  <UserManagementPage currentUser={runtimeContext?.server_user ?? null} />
                </ErrorBoundary>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
