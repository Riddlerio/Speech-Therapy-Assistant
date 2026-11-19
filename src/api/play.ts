import { api } from './client'

export const getProfile = (code: string) => api<{ heroName: string }>(`/play/children/${code}/profile`)
