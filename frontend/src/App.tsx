import { useState, useEffect, Component, type ReactNode } from 'react';
import ImportPage from './pages/ImportPage';
import FlightView from './pages/FlightView';
import ComparePage from './pages/ComparePage';
import ModelManager from './pages/ModelManager';
import { listFlights, listModels, listAircraft, type Flight, type AircraftModel, type Aircraft } from './api';

type Tab = 'import' | 'models' | 'flight' | 'compare';

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

export default function App() {
  const [tab, setTab] = useState<Tab>('flight');
  const [flights, setFlights] = useState<Flight[]>([]);
  const [selectedFlightId, setSelectedFlightId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [modelsVersion, setModelsVersion] = useState(0);

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
      if (data.aircraft.length > 0) {
        setSelectedAircraftId(data.aircraft[0].id);
      } else {
        setSelectedAircraftId(null);
      }
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

  useEffect(() => {
    const init = async () => {
      const [modelsData, flightsData] = await Promise.all([listModels(), listFlights()]);
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
      setLoading(false);
    };
    init();
  }, []);

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
        {flights.length > 0 && (
          <span className="text-xs text-gray-400">{flights.length} 架次已导入</span>
        )}
      </header>
      <main className="flex-1 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-full text-gray-400">加载中...</div>
        ) : (
          <>
            {/* Use visibility + absolute positioning for keep-alive instead of
                display:contents which breaks CSS layout (h-full, flex children)
                and causes ECharts container dimension failures in WebView2.
                Only the active tab is in-flow; hidden tabs are positioned off-screen
                so they stay mounted (preserving state) but don't affect layout. */}
            <div className={tab === 'import' ? 'h-full' : 'invisible absolute inset-0 overflow-hidden'}>
              <ErrorBoundary>
                <ImportPage onImported={onDataChanged} />
              </ErrorBoundary>
            </div>
            <div className={tab === 'flight' ? 'h-full' : 'invisible absolute inset-0 overflow-hidden'}>
              <ErrorBoundary>
                <FlightView
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
                />
              </ErrorBoundary>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
