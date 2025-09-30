idf_component_register(SRCS "hwTest.cpp" 
                       INCLUDE_DIRS "."
                       REQUIRES freertos esp_timer driver esp_netif esp_eth lwip mynet ui)
