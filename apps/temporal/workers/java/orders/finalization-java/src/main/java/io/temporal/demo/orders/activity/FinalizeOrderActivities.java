package io.temporal.demo.orders.activity;

import io.opentelemetry.api.metrics.LongCounter;
import io.opentelemetry.api.metrics.Meter;
import io.temporal.demo.orders.shared.ContractGate;
import io.temporal.demo.orders.shared.TemporalIds;
import io.temporal.spring.boot.ActivityImpl;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import orders.activities.v1.Activities.FinalizeOrderRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
@ActivityImpl(taskQueues = TemporalIds.ORDERS_FINALIZATION_TASK_QUEUE)
public class FinalizeOrderActivities implements FinalizeOrderActivity {

  private static final Logger log = LoggerFactory.getLogger(FinalizeOrderActivities.class);

  private final String ordersServiceUrl;
  private final HttpClient httpClient;
  private final LongCounter ordersFinalized;

  public FinalizeOrderActivities(
      @Value("${orders.service-url}") String ordersServiceUrl, Meter businessMeter) {
    this.ordersServiceUrl = ordersServiceUrl.replaceAll("/$", "");
    this.httpClient = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
    this.ordersFinalized =
        businessMeter.counterBuilder("orders.finalized").setDescription("Orders finalized").build();
  }

  @Override
  public void finalizeOrder(FinalizeOrderRequest req) {
    ContractGate.gate(req);
    String orderId = req.getOrderId();
    MDC.put("order_id", orderId);

    HttpRequest request =
        HttpRequest.newBuilder()
            .uri(URI.create(ordersServiceUrl + "/internal/orders/" + orderId + "/finalize"))
            .timeout(Duration.ofSeconds(5))
            .POST(HttpRequest.BodyPublishers.noBody())
            .build();

    try {
      HttpResponse<Void> response =
          httpClient.send(request, HttpResponse.BodyHandlers.discarding());
      if (response.statusCode() < 200 || response.statusCode() >= 300) {
        throw new IOException("finalize_order failed: HTTP " + response.statusCode());
      }
    } catch (IOException e) {
      throw new RuntimeException(e);
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      throw new RuntimeException(e);
    }

    ordersFinalized.add(1);
    log.info("order finalized");
  }
}
