/* STUB: the home part */
/* The "Why Hal did this" dialog. This part is being built; until then it shows the stub card. */
import { stubDialog } from "../ui/stub.js";

export const STUB = "home";
const Stub = stubDialog("Why Hal did this", "home");
Stub.drawer = true;
export default Stub;
