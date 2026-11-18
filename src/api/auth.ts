// 테스트(node 환경)에는 sessionStorage가 없어서 빈 토큰으로 본다.
export const getToken = () => (typeof sessionStorage === 'undefined' ? null : sessionStorage.getItem('speechHero.csrf')) || ''
export const setToken = (token: string) => sessionStorage.setItem('speechHero.csrf', token)
export const clearToken = () => sessionStorage.removeItem('speechHero.csrf')
