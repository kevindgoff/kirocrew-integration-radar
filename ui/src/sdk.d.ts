// Minimal typings for the host-provided SDK (served through the dashboard's import
// map, never installed from npm). Extend as more of the surface is used.
declare module '@kirocrew/app-sdk' {
  export interface AppApi {
    get<T = unknown>(path: string): Promise<T>
    post<T = unknown>(path: string, body?: unknown): Promise<T>
    put<T = unknown>(path: string, body?: unknown): Promise<T>
    delete<T = unknown>(path: string): Promise<T>
  }
  export function useAppApi(): AppApi
}

declare module '@kirocrew/app-sdk/ui' {
  import type { ReactNode } from 'react'
  export function Card(props: { children?: ReactNode; className?: string }): JSX.Element
  export function CardTitle(props: { children?: ReactNode }): JSX.Element
  export function PageHeader(props: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }): JSX.Element
  export function Badge(props: { variant?: 'ok' | 'err' | 'warn' | 'aim' | 'muted'; children?: ReactNode }): JSX.Element
}
