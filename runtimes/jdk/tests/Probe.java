import java.io.FileOutputStream;
import java.io.IOException;
import java.net.ConnectException;
import java.net.NoRouteToHostException;
import java.net.SocketTimeoutException;
import java.net.URI;
import java.net.UnknownHostException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.InvalidAlgorithmParameterException;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.cert.CertPathBuilderException;
import java.security.cert.CertPathValidatorException;
import java.security.cert.Certificate;
import java.security.cert.CertificateExpiredException;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.HexFormat;
import java.util.StringJoiner;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLPeerUnverifiedException;
import javax.net.ssl.TrustManager;
import javax.net.ssl.TrustManagerFactory;
import javax.net.ssl.X509TrustManager;

/**
 * The Java client of the JDK trust tests. Each mode prints one JSON line.
 *
 * connect URL: uses the default TLS trust of this JVM, as any Java program does.
 * anchors: prints the SHA-256 of each root that the default trust manager accepts.
 * store PEM OUT PASSWORD: writes a PKCS12 trust store with each certificate of a PEM file.
 */
public class Probe {
    public static void main(String[] args) throws Exception {
        switch (args[0]) {
            case "connect" -> connect(args[1]);
            case "anchors" -> anchors();
            case "store" -> store(args[1], args[2], args[3]);
            default -> throw new IllegalArgumentException(args[0]);
        }
    }

    static void connect(String url) throws Exception {
        var connection = (HttpsURLConnection) new URI(url).toURL().openConnection();
        connection.setConnectTimeout(15000);
        connection.setReadTimeout(15000);
        connection.setRequestMethod("HEAD");
        try {
            connection.getResponseCode();
            print(true, "OK", "");
        } catch (Exception failure) {
            print(false, code(failure), String.valueOf(failure));
        }
    }

    /** Returns one word for a failure. The trust tests decide on this word, not on message text. */
    static String code(Throwable failure) {
        for (Throwable t = failure; t != null; t = t.getCause()) {
            if (t instanceof InvalidAlgorithmParameterException
                    && String.valueOf(t.getMessage()).contains("trustAnchors")) {
                return "NO_TRUST_ANCHORS";
            }
            if (t instanceof CertificateExpiredException) {
                return "CERT_EXPIRED";
            }
            if (t instanceof CertPathBuilderException || t instanceof CertPathValidatorException) {
                return "PATH_NOT_TRUSTED";
            }
            if (t instanceof SSLPeerUnverifiedException) {
                return "HOSTNAME";
            }
            if (t instanceof UnknownHostException
                    || t instanceof ConnectException
                    || t instanceof NoRouteToHostException
                    || t instanceof SocketTimeoutException) {
                return "NETWORK";
            }
        }
        return failure instanceof IOException ? "IO" : "OTHER";
    }

    static void anchors() throws Exception {
        var factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        factory.init((KeyStore) null);
        var hashes = new StringJoiner("\",\"", "[\"", "\"]").setEmptyValue("[]");
        for (TrustManager manager : factory.getTrustManagers()) {
            if (manager instanceof X509TrustManager x509) {
                for (X509Certificate root : x509.getAcceptedIssuers()) {
                    hashes.add(sha256(root));
                }
            }
        }
        System.out.println("{\"anchors\":" + hashes + "}");
    }

    static String sha256(Certificate certificate) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(certificate.getEncoded());
        return HexFormat.of().formatHex(digest);
    }

    static void store(String pem, String out, String password) throws Exception {
        var keyStore = KeyStore.getInstance("PKCS12");
        keyStore.load(null, password.toCharArray());
        int count = 0;
        try (var input = Files.newInputStream(Path.of(pem))) {
            for (Certificate cert : CertificateFactory.getInstance("X.509").generateCertificates(input)) {
                keyStore.setCertificateEntry("cert-" + count++, cert);
            }
        }
        try (var output = new FileOutputStream(out)) {
            keyStore.store(output, password.toCharArray());
        }
        System.out.println("{\"entries\":" + count + "}");
    }

    static void print(boolean trusted, String code, String detail) {
        String clean = detail.replace("\\", "/").replace("\"", "'").replace("\n", " ");
        System.out.println("{\"trusted\":" + trusted + ",\"code\":\"" + code + "\",\"detail\":\"" + clean + "\"}");
    }
}
