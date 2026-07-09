package io.temporal.demo.orders.activity;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URI;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.actuate.health.Health;
import org.springframework.boot.actuate.health.HealthIndicator;
import org.springframework.stereotype.Component;

/** ADR-0016 startup gate — TCP reachability of orders-service (Java worker has no mock-api dep). */
@Component("ordersService")
public class OrdersServiceHealthIndicator implements HealthIndicator {

  private final URI ordersServiceUri;

  public OrdersServiceHealthIndicator(@Value("${orders.service-url}") String ordersServiceUrl) {
    this.ordersServiceUri = URI.create(ordersServiceUrl);
  }

  @Override
  public Health health() {
    String host = ordersServiceUri.getHost();
    int port = ordersServiceUri.getPort();
    if (port < 0) {
      port = "https".equalsIgnoreCase(ordersServiceUri.getScheme()) ? 443 : 80;
    }
    if (host == null || host.isBlank()) {
      return Health.down().withDetail("reason", "ORDERS_SERVICE_URL has no host").build();
    }
    try (Socket socket = new Socket()) {
      socket.connect(new InetSocketAddress(host, port), 2000);
      return Health.up().build();
    } catch (IOException e) {
      return Health.down(e).build();
    }
  }
}
