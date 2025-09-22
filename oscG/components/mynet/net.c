#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_system.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "driver/gpio.h"
#include "esp_eth.h"
#include "lwip/err.h"
#include "lwip/sockets.h"
#include "lwip/sys.h"
#include "lwip/netdb.h"
#include "lwip/dns.h"
#include "lwip/ip_addr.h"  // Added for IPSTR and IP2STR
#include "mynet.h"

#define ETH_PHY_POWER 12  // Olimex ESP32-POE-ISO power pin
#define ETH_PHY_RST_GPIO 16  // Olimex PHY reset pin (verify with schematic)
#define ETH_PHY_ADDR 0    // Olimex default
#define ETH_MDC_GPIO 23   // Olimex MDC
#define ETH_MDIO_GPIO 18  // Olimex MDIO

static SemaphoreHandle_t s_semph_get_ip_addrs = NULL;
esp_eth_handle_t s_eth_handle = NULL;  // Made non-static for mynet.h visibility if needed
esp_netif_t* s_netif;  // Single definition, initialized to NULL by default
esp_eth_netif_glue_handle_t s_eth_glue = NULL;

static void eth_event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
    if (event_base == ETH_EVENT && event_id == ETHERNET_EVENT_CONNECTED) {
        printf("Ethernet Link Up\n");
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_ETH_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*)event_data;
        printf("DEBUG: IP Event Received - Ethernet Got IP: " IPSTR "\n", IP2STR(&event->ip_info.ip));
        xSemaphoreGive(s_semph_get_ip_addrs);  // Give only when IP is assigned
    }
}

static void start(void) {
    if (s_semph_get_ip_addrs == NULL) {
        s_semph_get_ip_addrs = xSemaphoreCreateCounting(1, 0);
        if (s_semph_get_ip_addrs == NULL) {
            printf("Failed to create semaphore\n");
            return;
        }

        ESP_ERROR_CHECK(esp_event_handler_instance_register(ETH_EVENT, ETHERNET_EVENT_CONNECTED,
                                                           &eth_event_handler, NULL, NULL));
        ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_ETH_GOT_IP,
                                                           &eth_event_handler, NULL, NULL));
    }
}

esp_err_t net_connect(void) {
    start();

    // Configure and power on PHY with debug
    gpio_config_t io_conf = {
        .intr_type = GPIO_INTR_DISABLE,
        .mode = GPIO_MODE_OUTPUT,
        .pin_bit_mask = (1ULL << ETH_PHY_POWER) | (1ULL << ETH_PHY_RST_GPIO),
        .pull_down_en = 0,
        .pull_up_en = 0
    };
    ESP_ERROR_CHECK(gpio_config(&io_conf));

    printf("Applying PHY reset on GPIO%d...\n", ETH_PHY_RST_GPIO);
    ESP_ERROR_CHECK(gpio_set_level(ETH_PHY_RST_GPIO, 0)); // Active low reset
    vTaskDelay(100 / portTICK_PERIOD_MS); // Hold reset for 100ms
    ESP_ERROR_CHECK(gpio_set_level(ETH_PHY_RST_GPIO, 1)); // Release reset
    printf("PHY reset released.\n");
    vTaskDelay(100 / portTICK_PERIOD_MS); // Wait after reset

    printf("Powering on PHY on GPIO%d...\n", ETH_PHY_POWER);
    ESP_ERROR_CHECK(gpio_set_level(ETH_PHY_POWER, 1));
    vTaskDelay(1500 / portTICK_PERIOD_MS); // Delay for PHY stabilization
    printf("PHY power delay complete.\n");

    eth_mac_config_t mac_config = ETH_MAC_DEFAULT_CONFIG();

    eth_esp32_emac_config_t esp32_emac_config = ETH_ESP32_EMAC_DEFAULT_CONFIG();
    esp32_emac_config.smi_mdc_gpio_num = ETH_MDC_GPIO;  // Olimex MDC
    esp32_emac_config.smi_mdio_gpio_num = ETH_MDIO_GPIO; // Olimex MDIO
    esp32_emac_config.clock_config.rmii.clock_mode = EMAC_CLK_OUT; // Generic clock output
    esp32_emac_config.clock_config.rmii.clock_gpio = 17; // Olimex default clock out on GPIO17

    esp_eth_mac_t* mac = esp_eth_mac_new_esp32(&esp32_emac_config, &mac_config);

    eth_phy_config_t phy_config = ETH_PHY_DEFAULT_CONFIG(); // Correct macro for PHY config
    phy_config.phy_addr = ETH_PHY_ADDR;
    phy_config.reset_gpio_num = ETH_PHY_RST_GPIO;
    esp_eth_phy_t* phy = esp_eth_phy_new_lan87xx(&phy_config); // Use lan87xx for LAN8720 (compatible with LAN8710A)

    esp_eth_config_t config = ETH_DEFAULT_CONFIG(mac, phy); // Correct usage with MAC and PHY
    ESP_ERROR_CHECK(esp_eth_driver_install(&config, &s_eth_handle));

    esp_netif_config_t cfg = ESP_NETIF_DEFAULT_ETH();
    s_netif = esp_netif_new(&cfg);
    if (s_netif == NULL) {
        printf("DEBUG: Failed to create netif\n");
        return ESP_FAIL;
    }
    s_eth_glue = esp_eth_new_netif_glue(s_eth_handle);
    ESP_ERROR_CHECK(esp_netif_attach(s_netif, s_eth_glue));

    ESP_ERROR_CHECK(esp_eth_start(s_eth_handle));

    printf("Waiting for IP assignment...\n");
    if (xSemaphoreTake(s_semph_get_ip_addrs, pdMS_TO_TICKS(10000)) != pdTRUE) {
        printf("IP assignment timeout\n");
        return ESP_FAIL;
    }

    printf("Ethernet connected with IP assigned, s_netif: %p\n", (void*)s_netif);
    return ESP_OK;
}

esp_err_t net_disconnect(void) {
    esp_err_t ret = ESP_OK;
    if (s_eth_handle != NULL) {
        ESP_ERROR_CHECK(esp_eth_stop(s_eth_handle));
        ESP_ERROR_CHECK(esp_eth_del_netif_glue(s_eth_glue));
        ESP_ERROR_CHECK(esp_eth_driver_uninstall(s_eth_handle));
        esp_netif_destroy(s_netif);
        s_eth_handle = NULL;
        s_netif = NULL;
        s_eth_glue = NULL;
    }
    if (s_semph_get_ip_addrs != NULL) {
        if (uxSemaphoreGetCount(s_semph_get_ip_addrs) == 0) {
            vSemaphoreDelete(s_semph_get_ip_addrs);
            s_semph_get_ip_addrs = NULL;
        } else {
            ret = ESP_FAIL;
        }
    }
    return ret;
}