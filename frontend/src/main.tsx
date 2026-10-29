import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
import { authToken } from "./lib";

// 홈 화면에 추가할 때 시작 주소에 접속 토큰이 들어가도록 (아이폰 홈 화면 앱은 사파리와 저장소가 따로)
const tok = authToken();
const mf = document.querySelector<HTMLLinkElement>('link[rel="manifest"]');
if (tok && mf) mf.href = `/manifest.webmanifest?t=${encodeURIComponent(tok)}`;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
