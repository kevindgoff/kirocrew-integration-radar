// Typings for the host-provided SDK, which the dashboard serves through its import map
// (never installed from npm). Props were checked against Crew 0.7.2's host bundle.
declare module '@kirocrew/app-sdk' {
  export interface AppApi {
    get<T = unknown>(path: string, init?: RequestInit): Promise<T>
    post<T = unknown>(path: string, body?: unknown, init?: RequestInit): Promise<T>
    put<T = unknown>(path: string, body?: unknown, init?: RequestInit): Promise<T>
    patch<T = unknown>(path: string, body?: unknown, init?: RequestInit): Promise<T>
    del<T = unknown>(path: string, init?: RequestInit): Promise<T>
  }
  export function useAppApi(): AppApi
  export function useNotify(): (message: string, extra?: Record<string, unknown>) => void
  export function useNavBadge(): (count: number) => void
}

declare module '@kirocrew/app-sdk/ui' {
  import type { ButtonHTMLAttributes, HTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'

  export function Card(props: HTMLAttributes<HTMLDivElement>): JSX.Element
  export function CardTitle(props: HTMLAttributes<HTMLDivElement>): JSX.Element
  export function Btn(props: ButtonHTMLAttributes<HTMLButtonElement> & { primary?: boolean; danger?: boolean }): JSX.Element
  export function Input(props: InputHTMLAttributes<HTMLInputElement>): JSX.Element
  export function Badge(
    props: HTMLAttributes<HTMLSpanElement> & { variant?: 'ok' | 'err' | 'warn' | 'aim' | 'muted' },
  ): JSX.Element
  export function StatCard(props: {
    label: ReactNode
    value: ReactNode
    accent?: boolean
    onClick?: () => void
    active?: boolean
    title?: string
    className?: string
  }): JSX.Element
  export function EmptyState(props: { icon?: ReactNode; title: ReactNode; subtitle?: ReactNode; action?: ReactNode }): JSX.Element
  export function PageHeader(props: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }): JSX.Element
  export function Toggle(props: {
    checked: boolean
    onChange: (checked: boolean) => void
    disabled?: boolean
    label?: ReactNode
  }): JSX.Element
  export interface Segment<K extends string = string> {
    key: K
    label: ReactNode
    icon?: ReactNode
    count?: number
    disabled?: boolean
  }
  export function SegmentedControl<K extends string = string>(props: {
    segments: Segment<K>[]
    value: K
    onChange: (key: K) => void
    compact?: boolean
    collapse?: boolean
  }): JSX.Element
  export function Modal(props: {
    open: boolean
    onClose: () => void
    title?: ReactNode
    footer?: ReactNode
    maxWidth?: number
    children?: ReactNode
  }): JSX.Element
  export function MarkdownRenderer(props: { content: string }): JSX.Element
  export function ErrorNotice(props: { message: ReactNode; title?: ReactNode; onDismiss?: () => void }): JSX.Element
}

// The host re-exports only these icons by name (vendor/lucide-react.mjs in Crew 0.7.2);
// declaring just these makes the compiler reject an icon the host would not provide.
declare module 'lucide-react' {
  import type { SVGProps } from 'react'
  type Icon = (props: SVGProps<SVGSVGElement> & { size?: number | string }) => JSX.Element
  export const AlertTriangle: Icon, ArrowLeft: Icon, ArrowRight: Icon, ArrowUp: Icon, Bell: Icon, Bot: Icon,
    Brain: Icon, Building2: Icon, Calendar: Icon, Check: Icon, ChevronRight: Icon, Clock: Icon, Code: Icon,
    Download: Icon, ExternalLink: Icon, Gamepad2: Icon, Heart: Icon, Home: Icon, Loader2: Icon, Menu: Icon,
    MessageSquare: Icon, Moon: Icon, Package: Icon, Plug: Icon, Plus: Icon, Power: Icon, RefreshCw: Icon,
    Rocket: Icon, Search: Icon, Settings: Icon, Shield: Icon, Sparkles: Icon, Star: Icon, Sun: Icon, Tag: Icon,
    Trash2: Icon, Users: Icon, Wand2: Icon, Waves: Icon, X: Icon, Zap: Icon
}
