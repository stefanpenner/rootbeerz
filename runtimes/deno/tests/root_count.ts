// Prints the number of roots that node:tls reports.
import tls from "node:tls";

console.log(tls.rootCertificates.length);
