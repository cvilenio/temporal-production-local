package io.temporal.demo.appkit;

import io.grpc.netty.shaded.io.grpc.netty.GrpcSslContexts;
import io.grpc.netty.shaded.io.netty.handler.ssl.SslContext;
import io.grpc.netty.shaded.io.netty.handler.ssl.SslContextBuilder;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.Security;
import java.security.cert.Certificate;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import javax.net.ssl.TrustManager;
import javax.net.ssl.TrustManagerFactory;
import javax.net.ssl.X509TrustManager;
import org.bouncycastle.jce.provider.BouncyCastleProvider;
import org.bouncycastle.openssl.PEMKeyPair;
import org.bouncycastle.openssl.PEMParser;
import org.bouncycastle.openssl.jcajce.JcaPEMKeyConverter;

/** OSS/Cloud mTLS helper — loads cert-manager PEM keys (PKCS#8 or SEC1 EC). */
final class AppkitSslContexts {

  static {
    if (Security.getProvider(BouncyCastleProvider.PROVIDER_NAME) == null) {
      Security.addProvider(new BouncyCastleProvider());
    }
  }

  private AppkitSslContexts() {}

  static SslContext clientContext(String certPath, String keyPath, String caPath) throws Exception {
    X509Certificate clientCert = loadCertificate(certPath);
    PrivateKey privateKey = loadPrivateKey(keyPath);
    SslContextBuilder builder = SslContextBuilder.forClient().keyManager(privateKey, clientCert);
    if (caPath != null && !caPath.isBlank()) {
      builder.trustManager(trustManagerFromCa(caPath));
    }
    return GrpcSslContexts.configure(builder).build();
  }

  private static X509Certificate loadCertificate(String certPath) throws Exception {
    CertificateFactory factory = CertificateFactory.getInstance("X.509");
    try (InputStream in = new FileInputStream(certPath)) {
      return (X509Certificate) factory.generateCertificate(in);
    }
  }

  private static PrivateKey loadPrivateKey(String keyPath) throws Exception {
    try (PEMParser parser =
        new PEMParser(new InputStreamReader(new FileInputStream(keyPath), StandardCharsets.UTF_8))) {
      Object parsed = parser.readObject();
      JcaPEMKeyConverter converter = new JcaPEMKeyConverter().setProvider("BC");
      if (parsed instanceof PEMKeyPair pair) {
        return converter.getPrivateKey(pair.getPrivateKeyInfo());
      }
      if (parsed instanceof org.bouncycastle.asn1.pkcs.PrivateKeyInfo info) {
        return converter.getPrivateKey(info);
      }
      throw new IllegalArgumentException("Unsupported private key PEM in " + keyPath);
    }
  }

  private static TrustManager trustManagerFromCa(String caPath) throws Exception {
    CertificateFactory factory = CertificateFactory.getInstance("X.509");
    try (InputStream ca = new FileInputStream(caPath)) {
      Certificate cert = factory.generateCertificate(ca);
      KeyStore keyStore = KeyStore.getInstance(KeyStore.getDefaultType());
      keyStore.load(null, null);
      keyStore.setCertificateEntry("ca", cert);
      TrustManagerFactory tmf =
          TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
      tmf.init(keyStore);
      for (TrustManager tm : tmf.getTrustManagers()) {
        if (tm instanceof X509TrustManager) {
          return tm;
        }
      }
      throw new IllegalStateException("No X509TrustManager derived from server CA");
    }
  }
}
