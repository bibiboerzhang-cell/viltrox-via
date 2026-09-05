import { deny } from "./fixture-state";

// Install before importing any real component. Module/CSS loads remain same-origin,
// but application fetch/XHR/stream/worker APIs cannot reach any backend or provider.
if (window.location.origin !== "http://127.0.0.1:4178") throw new Error("Fixture requires http://127.0.0.1:4178");
window.fetch = async () => deny("fetch");
class BlockedConnection { constructor() { deny("XHR/WebSocket/EventSource/Worker"); } }
Object.defineProperty(window, "XMLHttpRequest", { value: BlockedConnection });
Object.defineProperty(window, "WebSocket", { value: BlockedConnection });
Object.defineProperty(window, "EventSource", { value: BlockedConnection });
Object.defineProperty(window, "Worker", { value: BlockedConnection });
Object.defineProperty(window, "SharedWorker", { value: BlockedConnection });
Object.defineProperty(navigator, "sendBeacon", { value: () => deny("sendBeacon") });
if (navigator.serviceWorker) Object.defineProperty(navigator.serviceWorker, "register", { value: async () => deny("serviceWorker.register") });
const confirmSynthetic = window.confirm.bind(window);
window.confirm = (message) => confirmSynthetic(`【合成数据夹具】仅模拟内存状态，不会连接业务系统或产生真实费用。\n\n真实组件原提示：\n${message}`);
void import("./App").then(({ mountFixture }) => mountFixture());
