"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";

export type FlowStep = 0 | 1 | 2;
export type CompareSide = "chat" | "quack";

export type DuckScreenAnchor = {
  /** Center X in CSS px inside viewport */
  x: number;
  /** Center Y in CSS px inside viewport */
  y: number;
  /** Approximate visual diameter in CSS px */
  size: number;
  /** Beak tip X in CSS px inside viewport */
  beakX: number;
  /** Beak tip Y in CSS px inside viewport */
  beakY: number;
  /** Whether the hero duck is currently visible enough to interact with */
  visible: boolean;
};

export type LandingStageMutableState = {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
  /** Raw scroll progress 0..1 */
  scrollProgress: number;
  /** Raw scroll in viewport heights */
  scrollVh: number;
  /** Damped scroll progress 0..1 */
  smoothScrollProgress: number;
  /** Damped scroll in viewport heights */
  smoothScrollVh: number;
  /** Smoothed scroll velocity (vh/s) */
  velocity: number;
  flowStep: FlowStep;
  compareSide: CompareSide;
  hoveredFeature: number;
  duckAnchor: DuckScreenAnchor;
};

type QuackListener = (anchor: DuckScreenAnchor) => void;
type AnchorListener = (anchor: DuckScreenAnchor) => void;
type ProgressListener = (progress: number, scrollTop: number) => void;

type LandingStageContextValue = {
  scrollRef: RefObject<HTMLElement | null>;
  stateRef: RefObject<LandingStageMutableState>;
  flowStep: FlowStep;
  setFlowStep: (step: FlowStep) => void;
  compareSide: CompareSide;
  setCompareSide: (side: CompareSide) => void;
  hoveredFeature: number;
  setHoveredFeature: (idx: number) => void;
  activeSection: string;
  setActiveSection: (id: string) => void;
  quackCount: number;
  triggerQuack: () => void;
  subscribeQuack: (cb: QuackListener) => () => void;
  subscribeAnchor: (cb: AnchorListener) => () => void;
  emitAnchor: (anchor: DuckScreenAnchor) => void;
  subscribeProgress: (cb: ProgressListener) => () => void;
  emitProgress: (progress: number, scrollTop: number) => void;
  registerStageTrigger: (fn: (() => void) | null) => void;
};

const LandingStageContext = createContext<LandingStageContextValue | null>(null);

const INITIAL_ANCHOR: DuckScreenAnchor = {
  x: 0,
  y: 0,
  size: 260,
  beakX: 0,
  beakY: 0,
  visible: true,
};

export function LandingStageProvider({
  scrollRef,
  children,
}: {
  scrollRef: RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  const [flowStep, setFlowStepState] = useState<FlowStep>(0);
  const [compareSide, setCompareSideState] = useState<CompareSide>("quack");
  const [hoveredFeature, setHoveredFeatureState] = useState<number>(-1);
  const [activeSection, setActiveSection] = useState<string>("top");
  const [quackCount, setQuackCount] = useState<number>(0);

  const stateRef = useRef<LandingStageMutableState>({
    scrollTop: 0,
    scrollHeight: 1,
    clientHeight: 1,
    scrollProgress: 0,
    scrollVh: 0,
    smoothScrollProgress: 0,
    smoothScrollVh: 0,
    velocity: 0,
    flowStep: 0,
    compareSide: "quack",
    hoveredFeature: -1,
    duckAnchor: { ...INITIAL_ANCHOR },
  });

  const stageTriggerRef = useRef<(() => void) | null>(null);
  const quackListeners = useRef(new Set<QuackListener>());
  const anchorListeners = useRef(new Set<AnchorListener>());
  const progressListeners = useRef(new Set<ProgressListener>());

  const setFlowStep = useCallback((step: FlowStep) => {
    stateRef.current.flowStep = step;
    setFlowStepState(step);
  }, []);

  const setCompareSide = useCallback((side: CompareSide) => {
    stateRef.current.compareSide = side;
    setCompareSideState(side);
  }, []);

  const setHoveredFeature = useCallback((idx: number) => {
    stateRef.current.hoveredFeature = idx;
    setHoveredFeatureState(idx);
  }, []);

  const registerStageTrigger = useCallback((fn: (() => void) | null) => {
    stageTriggerRef.current = fn;
  }, []);

  const triggerQuack = useCallback(() => {
    stageTriggerRef.current?.();
    setQuackCount((c) => c + 1);
    const anchor = stateRef.current.duckAnchor;
    for (const cb of quackListeners.current) cb(anchor);
  }, []);

  const subscribeQuack = useCallback((cb: QuackListener) => {
    quackListeners.current.add(cb);
    return () => {
      quackListeners.current.delete(cb);
    };
  }, []);

  const subscribeAnchor = useCallback((cb: AnchorListener) => {
    anchorListeners.current.add(cb);
    return () => {
      anchorListeners.current.delete(cb);
    };
  }, []);

  const emitAnchor = useCallback((anchor: DuckScreenAnchor) => {
    stateRef.current.duckAnchor = anchor;
    for (const cb of anchorListeners.current) cb(anchor);
  }, []);

  const subscribeProgress = useCallback((cb: ProgressListener) => {
    progressListeners.current.add(cb);
    return () => {
      progressListeners.current.delete(cb);
    };
  }, []);

  const emitProgress = useCallback((progress: number, scrollTop: number) => {
    for (const cb of progressListeners.current) cb(progress, scrollTop);
  }, []);

  const value = useMemo<LandingStageContextValue>(
    () => ({
      scrollRef,
      stateRef,
      flowStep,
      setFlowStep,
      compareSide,
      setCompareSide,
      hoveredFeature,
      setHoveredFeature,
      activeSection,
      setActiveSection,
      quackCount,
      triggerQuack,
      subscribeQuack,
      subscribeAnchor,
      emitAnchor,
      subscribeProgress,
      emitProgress,
      registerStageTrigger,
    }),
    [
      scrollRef,
      flowStep,
      setFlowStep,
      compareSide,
      setCompareSide,
      hoveredFeature,
      setHoveredFeature,
      activeSection,
      quackCount,
      triggerQuack,
      subscribeQuack,
      subscribeAnchor,
      emitAnchor,
      subscribeProgress,
      emitProgress,
      registerStageTrigger,
    ],
  );

  return <LandingStageContext.Provider value={value}>{children}</LandingStageContext.Provider>;
}

export function useLandingStage() {
  const ctx = useContext(LandingStageContext);
  if (!ctx) throw new Error("useLandingStage must be used inside LandingStageProvider");
  return ctx;
}
