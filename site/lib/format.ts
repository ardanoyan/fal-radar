export const fmt = (n: number) => n.toLocaleString("en-US");
export const stars = (n: number) => `${fmt(n)} ${n === 1 ? "star" : "stars"}`;
export const avatar = (url: string, size = 80) => `${url}${url.includes("?") ? "&" : "?"}s=${size}`;
